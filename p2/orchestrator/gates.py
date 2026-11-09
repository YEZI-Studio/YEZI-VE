#!/usr/bin/env python3
# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""门禁：把 p1/series/检查项.md 里能用脚本量的条目做成检查。纯标准库 + ffmpeg，python3 -I 可运行；
有效运动量、实测卡点要 numpy / cv2 / librosa，放在引擎 Python 的子进程里量（p2/tools/effmotion.py、syncmeas.py）。

  python3 -I gates.py 片子.mp4 [--series chroma] [--beatmap beatmap.json] [--shotlist shotlist.json] [--final] [--out g.json]
                      [--py 引擎 Python] [--music inputs/music.json]

原则（检查项.md）：阈值来自认可片的实测或用户的话，超了只报警、给位置，不自动判死。
每条检查带 level（v2， 三档约束）：
  hard = 挡交付：工程底线（交付规格 1920×1080 / 60 fps / H.264 / 单视频流、帧数与镜头表、冻结区、配乐音轨 / BPM / 长度、
         计划时长 / 铺满 / 屏息 + DROP / 零件名）+ 镜内有效运动量 / 原地摆动、整片近乎静止帧（「拿抽搐、轻微摆动代替动效」）
         + 实测卡点（网格、最强 16 个重音、选择性、计划大卡点、交付混流；「卡点卡得不准」）+ 主角大字和数字不重复；
  warn = 其余一律只报警给位置（切点密度、第 1 秒、重击密度这些只奖励数量的门、意象、字号、英文、事件、转场声明、切点像素检查、
         整屏染色、相邻段落雷同、无声重击、静图 + 推拉……）。
status 只有 hard 的检查会是 fail；镜内运动量、卡点和重复在制作中途（final=False）先记 warn 给返修看（stages 的 motion_force /
sync_force 读 <视频>.eff.json、<视频>.sync.json 把相关镜头判不过），交付（final=True）才判 fail。
切点、重击、大变化帧、亮度跳变按 160×90 下采样、同一组阈值算，
所以数字能和本系列定稿片直接比；镜内帧差用 96×54 灰度量，
现在只报告，硬门禁换成有效运动量（一拍里原地来回的部分不算）。
"""
from __future__ import annotations

import argparse
import json
import math
import operator
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import media, util  # noqa: E402

W, H = 160, 90
NPX = W * H
TOOLS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
EFF_TOOL = os.path.join(TOOLS, "effmotion.py")          # 有效运动量（引擎 Python：numpy + cv2）
SYNC_TOOL = os.path.join(TOOLS, "syncmeas.py")          # 打击点表 / 实测卡点（引擎 Python：numpy + scipy + librosa）
class _ThreadErr:
    """子进程工具最近一次出错的原因（eff / sync / shot），门禁行里写「没量」时带上。按线程分开存：几镜并行时 shot_sync 在渲染锁外跑，
    一个线程的 _py_ok 不会清掉另一个线程刚记下的原因（用法和 dict 一样：get / pop / [] / in）。"""

    def __init__(self):
        self._l = threading.local()

    def _d(self):
        if not hasattr(self._l, "d"):
            self._l.d = {}
        return self._l.d

    def get(self, k, default=None):
        return self._d().get(k, default)

    def pop(self, k, *default):
        return self._d().pop(k, *default)

    def __getitem__(self, k):
        return self._d()[k]

    def __setitem__(self, k, v):
        self._d()[k] = v

    def __contains__(self, k):
        return k in self._d()

    def __bool__(self):
        return bool(self._d())


LAST_ERR = _ThreadErr()

# 硬门禁（挡交付）的检查 id；不在这里的检查最多 warn（_chk 会把 fail 降成 warn）。stages.py 用 _chk 造的 m_bpm、m_length 也在这里。
HARD = {"spec_file", "spec_size", "spec_fps", "spec_codec", "audio", "frames", "frozen", "m_bpm", "m_length",
        "p_duration", "p_cover", "p_breath", "p_parts",
        "motion_still", "p_repeat", "repeat",
        # v2.1（11-03 规则树接进来）：底线里机器量得稳的（定稿片 Light、点火都过）也挡交付；对照表在 lib/tree.py 的 VIDEO / STABLE
        "still", "similar", "colors", "grey", "first_motion", "end_black", "loudness",
        "tree_must",                     # 规则树的必做、计划 / 代码上的底线没做到（stages 交付时造）
        # 11-04（「拿抽搐、轻微摆动代替动效」「卡点卡得不准」）：旧的帧差 motion_shot 降成只报告，换成有效运动量 + 原地摆动；
        # 实测卡点（点火、Light 都过，试跑稿都不过）
        "motion_eff", "motion_sway",
        "sync_grid", "sync_plan", "sync_mux",           # 最强重音的倍数（sync_top）、选择性（sync_select）只报告，和返修循环同一口径
        "sync_cuts"}                                     # 计划切点真切出来（Codex 初剪 52 个只切了 13 个，开头一镜 5 秒一个构图）
# 交付时只有 ENGINEERING 里的硬门禁挡出片；其余硬门禁（质量项）在返修循环里照样判不过、发回，返修用完还没过就照样出片、
# 报告里标「交付时没过」（小瑕疵不挡出片）
# （11-05 DeepSeek 微调版只因最后一击 3.2 倍对 4 倍被挡，没出成片）。
ENGINEERING = {"spec_file", "spec_size", "spec_fps", "spec_codec", "audio", "frames", "frozen", "m_bpm", "m_length",
               "p_duration", "loudness", "sync_grid", "sync_mux"}

# v2 门禁的默认参数。series 的 gates 里写了同名键（motion / repeat / tint / sections）就逐项覆盖：按片调可以，偏离写 note 说理由。
V2 = {
    "motion": {
        "note": "镜内运动量（PPT 感）：96×54 灰度相邻帧平均绝对差（0–255）；d > max(cut_abs, cut_k×(全片中位+1)) 的是切点帧，不算镜内；"
                "镜内 d < still_eps 算近乎静止。每镜（去掉切点帧、屏息那一拍、最后一击之后的海报停留）的均值 ≥ 原片均值 × shot_min_ratio，"
                "整片镜内近乎静止帧 ≤ still_frac_max。",
        "size": [96, 54], "cut_abs": 14, "cut_k": 4, "still_eps": 0.5,
        "ref_mean": {"Light": 12.52, "点火": 10.44},
        "ref_pick": "min",
        "shot_min_ratio": 0.5,
        "still_frac_max": 0.12,
        "min_frames": 12,
        "window_f": 100,
        "tail_max_s": 3.0,
        "calibration": "原片实测：Light 切点 152、镜内均值 12.52、中位 8.45、近乎静止 10.5%；点火 120、10.44、7.53、3.2%；"
                       "每镜门槛取两条原片里低的点火：10.44 × 50% = 5.22。"
                       "按原片自己的镜头量：Light（没有对得上的镜头表，100 帧一段）11 段、点火（自己的 4 镜）都过；"
                       "按 100 帧滑窗（步长 5，不算屏息和最后一击之后）：Light 210 个窗 0 个低于 5.22（最低 7.55），"
                       "点火 292 个窗 17 个低于（16.9–18.6 s 最低 4.82；最后一击前那一小节 23.1–25.1 s 收着走，最低 2.5）——"
                       "两小节一镜的做法里，学点火那样在最后一击前收一小节会踩线，要按片调就在系列 gates.motion 里写理由。"
                       "v1 15 镜里 s04 4.86、s09 4.20、s12 1.91、s15 2.79 低于。"
                       "（11-04 起这条帧差线只报告：motion_shot；硬门禁换成下面 eff 的有效运动量。）",
        # 11-04 有效运动量（「拿抽搐、轻微摆动代替动效」）：p2/tools/effmotion.py，在引擎 Python 里量
        "eff": {
            "note": "有效运动量 eff（单位同帧差，0–255）：480×270 分 6×4 块，每块 phaseCorrelate 求平移，一拍窗口（±hwin 帧，硬切截断）里"
                    "净位移 / 路程 = 进展；eff = 帧差 × Σ 块权重 ×〔非刚性份额 + 刚性份额 × 进展〕，持续来回的块（路程 ≥ppath px/帧）块内残差也按来回算；"
                    "硬切、big / drop / final 之后 exempt_f 帧真衰减的震不罚。每镜（切点帧、屏息那一拍、最后一击之后不算）："
                    "motion_eff = eff ≥ ratio × 原片里最低的整片有效量；motion_sway = 原地来回占比 J = 1 − Σeff/Σ帧差 ≤ j_max，"
                    "且同素材缩放乒乓（同一张图在切点上宽紧来回跳）< pp_max；motion_content = 非刚性内容 < content_warn 只报警（静图 + 推拉嫌疑）。"
                    "sway_hard=false 时 motion_sway 交付也只记 warn（只在返修循环里强制判不过）。"
                    "11-04 验收补：整屏亮度来回（每拍明暗呼吸、闪烁）从 eff / content 里扣掉（不进 J）；motion_sway 另加两条——"
                    "长窗口（±hwin_long 帧）原地来回占比 J_long ≤ j_long_max（周期 ≥ 2 s 的慢摆、漂浮）、回到旧构图（硬切 / 换构图后第一帧和"
                    "本镜更早、不相邻的段几乎同图）任一 100 帧窗 < ret_win 次，且整镜不到 ret_n 次或不到段数的 ret_frac（「同一版式每拍跳一下再回来」）。",
            "grid": [6, 4], "hwin": 24, "exempt_f": 8, "ppath": 3.0,
            "ref_eff": {"Light": 9.45, "点火": 7.60},
            "ratio": 0.57, "j_max": 0.45, "pp_max": 3, "content_warn": 2.5, "sway_hard": True, "timeout_s": 900,
            "hwin_long": 48, "j_long_max": 0.5, "ret_win": 6, "ret_n": 5, "ret_frac": 0.4,
            # 计划里的低谷镜头（节拍表里能量比前后两段都低的那一段，导演提示词「至少一段比前后两段都低」）线放低到 trough_ratio 倍：
            # 原片低谷本来就收着走（点火最后一击前收的那一小节 100 帧窗最低 2.5 ≥ 0.55 × 4.33 = 2.38）
            "trough_ratio": 0.55,
            "calibration": "按往期定稿片（Light、点火）整片实测定（去掉片尾 3 s 海报）：线 = 0.5 × 点火有效量 8.65 ≈ 4.33，扣掉来回的亮度后 ratio = 4.33 / 点火 7.60 ≈ 0.57；原片窗最高 J 离 0.45 都有 0.1 左右余量。合成作弊片（左右摆、缩放呼吸、整帧抖、静图闪、慢摆、每拍横移复位、两张图轮换）都判不过；动态粗颗粒、静图轮播 + 横移仍过，见风险。",
        },
    },
    # 11-04 实测卡点（「卡点卡得不准」）：p2/tools/syncmeas.py check，在引擎 Python 里量。键名和 syncmeas.THR 一一对应
    "sync": {
        "note": "画面帧差 160×90 RGB；音轨现场重算起音（cut_rN 没声音 → 配乐从 start_s 起按样点切；"
                "交付成片 → 自己的音轨），打击点表 music_map 只给网格、标签和计划对照。判 ON 的锚 = 起音后第一帧（g_on = ceil(起音帧)），"
                "ON = 第一下强变化（≥ max(2×±12 帧中位, 0.03)）在 g_on−1 或 g_on；对比度 = 那一帧帧差 / ±12 帧中位。"
                "sync_grid：起音 − 格点中位 |·| ≤ grid_ms、前后半差 ≤ grid_drift_ms、DROP 实测起音离计划 drop 帧 ≤ drop_f（防半拍 / 整拍错；"
                "没有 DROP 锚只记 warn）、现场起音对打击点表 ≤ map_dev_ms；sync_top：最强 top_n 个重音 ON ≥ top_on 且对比度 ≥ top_c 的 ≥ top_c_n；"
                "sync_select：重音 ON% − 平移半拍后 ON% 的均值 ≥ select（防「每个 8 分都切、到处抖」刷 ON%）；"
                "sync_plan：计划 big / drop / final 都 ON 且对比度 ≥ plan_c（final ≥ plan_c_big），落在无起音格或旁边有更强重音的只出问题单；"
                "sync_silent（只报警）：画面最重的 K 下（K = 片长秒数）落在 ±2 帧内没有起音的 ≤ silent_max；"
                "sync_mux（只在交付）：成片音轨对配乐四段互相关 |偏移| ≤ mux_lag_ms、相关 ≥ mux_corr；sync_on 默认集合 ON% 只报告。"
                "逐镜（stages 渲完每镜调 shot_sync）：重 ≥ shot_heavy〔倍, 最小帧差〕、轻 ≥ shot_light，表外无起音处 ≥ shot_silent 的强变化报出来。",
        "grid_ms": 25.0, "grid_drift_ms": 8.0, "drop_f": 1.0, "map_dev_ms": 8.0,
        "top_n": 16, "top_on": 13, "top_c": 3.0, "top_c_n": 10, "select": 0.12,
        "plan_c": 3.0, "plan_c_big": 4.0, "silent_max": 3, "onset_min": 0.6,
        "mux_lag_ms": 1.0, "mux_corr": 0.95,
        "shot_heavy": [3.0, 0.08], "shot_light": [2.0, 0.04], "shot_silent": [4.0, 0.08],
        "timeout_s": 900,
        "calibration": "按往期定稿片（Light、点火，各自的音轨）实测定：最强 16 ON / 对比度 ≥3 / 选择性都过，试跑稿都不过。稳健性：最强 N 换成 12 / 20 分界不变；把画面整体推晚 1 帧，最强 16 ON 掉到 5/16，被拦。有的原片 DROP 只有 3.3 倍，所以 drop 和 big 一样 ≥3，final ≥4。把配乐错开半拍，sync_grid 判不过。",
    },
    # 只奖励数量的密度门（11-04 起一律只报警，不管系列配置怎么写）：系列 gates 里同名键优先，没写用这里的
    "density": {
        "note": "切点密度、第 1 秒构图数、重击密度、计划切点密度只报警：切点改从音乐起音里取以后全片约 3.7–3.8 个/秒、前奏第 1 秒构图很少，"
                "这首歌起音上限 4.68 个/秒；拿数量当目标会逼出「同一张图挪一点、缩一点」的假切点（也就是「抽搐」）。",
        "cuts_per_s_min": 3.75, "first_second_min": 8, "hits_per_s": [4.6, 5.3], "hits_too_busy": 7.9,
        "plan_cuts_per_s_min": 3.8, "plan_first_second_min": 10,
    },
    "repeat": {
        "note": "镜头表里主角大字 hero_word、数字（hero_word + words 里的数）全片各出现在一个镜头；首尾两镜是同一张海报（CHROMA 首尾循环），"
                "只在这两镜里重复的算一次。数值相同、单位相同或有一边没写单位就算同一个数（100 和 100 °C 是一个数，100 °C 和 100 K 不是）。"
                "words 里的其他大词、小标签 label 里的数只报告。",
        "bookend": True,
    },
    "tint": {
        "note": "整屏染色（只报警）：每 6 帧取一帧（160×90），签名色相 ±hue_tol_deg、饱和度 ≥ 0.12 的像素占全屏 > frame_frac 算「整屏签名色」，"
                "其中饱和度 < 0.25 的淡色占全屏 > frame_frac 算「滤镜式」（颜色当滤镜）。签名色：topic 给了用 topic 的，没给取全片最多的色相。",
        "hue_tol_deg": 30, "frame_frac": 0.45, "share_max": 0.35, "pale_share_max": 0.02,
        "calibration": "整屏签名色帧占比：Light 26.4%、点火 21.7%、v1 18.5%（CHROMA 的冲击帧本来就整屏签名色，所以上限放在 35%）；"
                       "滤镜式帧占比：Light 0、点火 0、v1 3.8%（17.5–19.5 s 一带整屏淡青）。",
    },
    "sections": {
        "note": "相邻段落雷同（粗查，只报警）：段落 = 镜头表的 section（没有就用节拍表 sections，再没有按 win_s 秒切）。"
                "两段各自的采样帧（16×9 灰度，去掉没结构的平帧）在另一段里找得到 corr ≥ frame_corr 的近似帧的占比（两边平均）≥ dup_min，"
                "且主色直方图交集 ≥ color_min，就报「雷同」。",
        "win_s": 4.0, "frame_corr": 0.92, "dup_min": 0.5, "color_min": 0.8,
        "calibration": "Light、点火按 4 s 切段：相邻段近似帧占比最高 0.44（Light 片尾海报停留）、主色交集 0.35–0.96，都不报；"
                       "v1 六段相邻近似帧占比都是 0（v1 的重复是词和意象，不是同一构图，靠镜头表的 p_repeat / p_image 抓）。",
    },
}


def _cfg(g, key):
    return dict(V2[key], **((g or {}).get(key) or {}))


def _dens(g, key):
    """密度门的参数：系列 gates 里写了就用系列的，没写用 V2["density"]（这些门只报警）。"""
    v = (g or {}).get(key)
    return V2["density"][key] if v is None else v


# ----------------------------------------------------------------------------- 子进程量法（引擎 Python：有效运动量、实测卡点）
def _eff_cfg(mc):
    """V2["motion"]["eff"] 逐项被系列 gates.motion.eff 覆盖（系列只写一两个键也不丢其余的）。"""
    return dict(V2["motion"]["eff"], **((mc or {}).get("eff") or {}))


def _sync_cfg(cfg=None):
    return dict(V2["sync"], **(cfg or {}))


def eff_need(mc):
    """motion_eff 的线：ratio × 原片里最低的整片有效量（0.5 × 点火 8.65 = 4.33）。mc = 合并后的 motion 配置（含 mc["eff"]）。"""
    e = _eff_cfg(mc)
    return e["ratio"] * min(e["ref_eff"].values())


ENERGY = {"low": 0, "mid": 1, "high": 2, "peak": 3}


def trough_shots(beatmap, shotlist):
    """计划里的低谷镜头：节拍表 sections 里能量比前后两段都低的那一段（首尾两段不算：前奏、海报不是低谷）上的镜头 id。"""
    secs = (beatmap or {}).get("sections") or []
    lv = [ENERGY.get(str(x.get("energy")), 1) for x in secs]
    low = {secs[i]["id"] for i in range(1, len(secs) - 1) if lv[i] < lv[i - 1] and lv[i] < lv[i + 1]}
    return {x["id"] for x in (shotlist or {}).get("shots") or [] if x.get("section") in low}


def shot_need(mc, sid, troughs=()):
    """这一镜的 motion_eff 线：低谷镜头 = 线 × trough_ratio，其余照 eff_need。"""
    need = eff_need(mc)
    return need * float(_eff_cfg(mc).get("trough_ratio", 1.0)) if sid in set(troughs or ()) else need


def _franges(frames):
    """帧集合 → 'a-b,c-d'（每段 [a, b)，effmotion --exempt 的写法）。"""
    out, s, p = [], None, None
    for f in sorted({int(x) for x in frames}) + [None]:
        if f is not None and p is not None and f == p + 1:
            p = f
            continue
        if s is not None:
            out.append(f"{s}-{p + 1}")
        s = p = f
    return ",".join(out)


def _tmp_json(near, tag, obj):
    """给子进程的临时 JSON：放在视频旁边，名字带视频名 + 进程号 + 随机串（编排器会并行量不同的视频）。"""
    p = f"{near}.{tag}.{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
    with open(p, "w") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


def _py_ok(py, key):
    LAST_ERR.pop(key, None)
    if not py:
        LAST_ERR[key] = "没给引擎 Python"
    elif not os.path.exists(py):
        LAST_ERR[key] = f"引擎 Python 不存在：{py}"
    return key not in LAST_ERR


def _kill_pg(p):
    """子进程用 start_new_session 起的：整组 SIGTERM，3 秒不退 SIGKILL（effmotion / syncmeas 自己起的 ffmpeg 在同一组里）。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(p.pid, sig)
        except OSError:
            pass
        try:
            p.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            continue


def _run_proc(args, timeout):
    """跑子进程、收 stdout / stderr；等的时候每 0.5 s 查 util.HALT（Ctrl-C，11-04 晚：停集以后量法工具也要马上收工）和超时。
    返回 (退出码 | None, stdout, stderr, None | "timeout" | "interrupted")。"""
    if util.HALT.is_set():
        return None, "", "", "interrupted"
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True,
                         env=media.ENV, start_new_session=True)
    t0 = time.time()
    try:
        while True:
            try:
                out, err = p.communicate(timeout=0.5)
                return p.returncode, out or "", err or "", None
            except subprocess.TimeoutExpired:
                why = "interrupted" if util.HALT.is_set() else ("timeout" if time.time() - t0 > timeout else None)
                if why:
                    _kill_pg(p)
                    try:
                        out, err = p.communicate(timeout=5)
                    except (subprocess.TimeoutExpired, ValueError, OSError):
                        out, err = "", ""
                    return None, out or "", err or "", why
    except BaseException:
        _kill_pg(p)
        raise


def _run_tool(key, args, timeout, want_out=False):
    """跑引擎 Python 子进程；出错返回 False（want_out 时 None），原因（stderr 最后几行）记进 LAST_ERR[key]；want_out 时成功返回 stdout。"""
    try:
        rc, out, err, why = _run_proc(args, timeout)
    except OSError as e:
        LAST_ERR[key] = f"{type(e).__name__}: {e}"[:300]
        return None if want_out else False
    if why:
        LAST_ERR[key] = "Ctrl-C：编排器收工，没量完" if why == "interrupted" else f"TimeoutExpired: 超过 {timeout} s"
        return None if want_out else False
    if rc != 0:
        tail = (err or out or "").strip().splitlines()[-3:]
        LAST_ERR[key] = f"{os.path.basename(args[2])} 退出码 {rc}：" + " | ".join(tail)[:300]
        return None if want_out else False
    return out if want_out else True


# 死区（渲完每镜马上量，发回时告诉代理哪几段几乎不动、那几段里块速度多少、6×4 分块里哪几块在动）：
# 读 effmotion 的逐帧缓存（<视频>.eff.npz：d96、每块平移 sh、每块帧差 raw），不重新提取。
DEAD_PY = r'''
import json, sys
import numpy as np
z = np.load(sys.argv[1], allow_pickle=False)
a, b, off, dmax, nmin = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5]), int(sys.argv[6])
ex = set(json.loads(sys.argv[7]))
d, sh, raw = z["d"], z["sh"], z["raw"]
N = len(d)
b = N if b <= 0 else min(b, N)
runs, s = [], None
for t in range(max(1, a + 1), b + 1):
    still = t < b and float(d[t]) < dmax and t not in ex
    if still and s is None:
        s = t
    elif not still and s is not None:
        if t - s >= nmin:
            runs.append((s, t))
        s = None
out = []
gx, gy = 6, 4
for s, e in runs:
    sp = np.hypot(sh[s:e, :, 0], sh[s:e, :, 1])
    bs = sp.mean(0)
    br = raw[s:e].mean(0) * 255.0
    mv = [bool(bs[k] >= 0.5 or br[k] >= 1.0) for k in range(len(bs))]
    grid = " / ".join("".join("■" if mv[j * gx + i] else "·" for i in range(gx)) for j in range(gy))
    out.append({"a": int(s + off), "b": int(e - 1 + off), "n": int(e - s), "d96": round(float(d[s:e].mean()), 2),
                "px_f": round(float(np.median(sp.mean(1))), 2), "px_f_max": round(float(bs.max()), 2),
                "moving": int(sum(mv)), "grid": grid})
print(json.dumps(out))
'''


def eff_deadzones(video, py, a=0, b=None, offset=0, exempt=(), d_max=1.0, min_len=12):
    """几乎不动的帧段：[a, b)（这条视频的帧号）里 d96 < d_max 且连续 ≥ min_len 帧、不含 exempt 的帧。要先跑过 eff_stats（读它的
    <视频>.eff.npz）。返回 [{a, b（含，帧号 + offset = 片内帧号）, n, d96, px_f（块速度中位，1920 px/帧）, px_f_max, moving, grid}]；
    grid = 6×4 分块、上到下 4 行（■ 在动，· 不动）。量不了返回 None，原因在 LAST_ERR["dead"]。"""
    cache = video + ".eff.npz"
    if not _py_ok(py, "dead"):
        return None
    if not os.path.exists(cache):
        LAST_ERR["dead"] = "没有 eff_stats 的逐帧缓存"
        return None
    out = _run_tool("dead", [py, "-I", "-c", DEAD_PY, cache, str(int(a)), str(int(b or 0)), str(int(offset)), repr(float(d_max)),
                             str(int(min_len)), json.dumps(sorted(int(x) for x in exempt or ()))], 120, want_out=True)
    if out is None:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        LAST_ERR["dead"] = f"死区输出读不了：{e}"
        return None


SLOW_PY = r'''
import json, sys
import numpy as np
z = np.load(sys.argv[1], allow_pickle=False)
a, b, off, win, k = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]), int(sys.argv[6])
ex = set(json.loads(sys.argv[7]))
d, sh, raw = z["d"], z["sh"], z["raw"]
N = len(d)
b = N if b <= 0 else min(b, N)
lo = max(1, a + 1)
cand = []
for s in range(lo, max(lo + 1, b - win + 1), max(1, win // 4)):
    e = min(b, s + win)
    idx = [t for t in range(s, e) if t not in ex]
    if e - s < win // 2 or len(idx) < (e - s) // 2:
        continue
    cand.append((float(np.mean(d[idx])), s, e))
allx = [t for t in range(lo, b) if t not in ex]
avg = float(np.mean(d[allx])) if allx else 0.0
cand = sorted(c for c in cand if c[0] < avg)          # 只报比这一镜平均还慢的窗
out, used = [], []
gx, gy = 6, 4
for m, s, e in cand:
    if len(out) >= k:
        break
    if any(s < u1 and u0 < e for u0, u1 in used):
        continue
    used.append((s, e))
    sp = np.hypot(sh[s:e, :, 0], sh[s:e, :, 1])
    bs = sp.mean(0)
    br = raw[s:e].mean(0) * 255.0
    mv = [bool(bs[j] >= 0.5 or br[j] >= 1.0) for j in range(len(bs))]
    grid = " / ".join("".join("■" if mv[j * gx + i] else "·" for i in range(gx)) for j in range(gy))
    out.append({"a": int(s + off), "b": int(e - 1 + off), "n": int(e - s), "d96": round(m, 2),
                "px_f": round(float(np.median(sp.mean(1))), 2), "px_f_max": round(float(bs.max()), 2),
                "moving": int(sum(mv)), "grid": grid, "slow": True})
print(json.dumps(sorted(out, key=lambda x: x["a"])))
'''


def eff_slowest(video, py, a=0, b=None, offset=0, exempt=(), win=24, k=3):
    """运动量最低的 k 个 win 帧窗（互不重叠，按帧差 d96 均值排，只报比这一镜平均慢的）：镜头慢但没停、死区（d96 < 1 连续 12 帧）抓不到时，
    逐镜发回里至少给出几段下手的地方（试跑里没过线的镜头一半是「慢但没停」）。字段和 eff_deadzones 一样，另加 slow=True。
    要先跑过 eff_stats（读 <视频>.eff.npz）。量不了返回 None，原因在 LAST_ERR["slow"]。"""
    cache = video + ".eff.npz"
    if not _py_ok(py, "slow"):
        return None
    if not os.path.exists(cache):
        LAST_ERR["slow"] = "没有 eff_stats 的逐帧缓存"
        return None
    out = _run_tool("slow", [py, "-I", "-c", SLOW_PY, cache, str(int(a)), str(int(b or 0)), str(int(offset)), str(int(win)), str(int(k)),
                             json.dumps(sorted(int(x) for x in exempt or ()))], 120, want_out=True)
    if out is None:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError) as e:
        LAST_ERR["slow"] = f"慢段输出读不了：{e}"
        return None


def eff_stats(video, groups, mc, py, hits=(), exempt=()):
    """有效运动量（p2/tools/effmotion.py，接口 2a）。groups = {名字: (起帧, 止帧)}（这条视频里的帧号，[起, 止)）；
    hits = big / drop / final 帧（冲击余波豁免）；exempt = 不算的帧（屏息那一拍、最后一击之后）。
    返回 {"film": {...}, "shots": {名字: {n, d96, eff, J, pingpong, content, ok_eff, ok_sway, …} 或 None（帧太少）}}，
    同时存到 video + ".eff.json"；逐帧提取缓存 video + ".eff.npz"（同一条视频换 groups 再量只要几秒）。
    py 没给 / 不存在 / 工具出错都返回 None，原因在 LAST_ERR["eff"]。"""
    if not _py_ok(py, "eff"):
        return None
    e, out = _eff_cfg(mc), video + ".eff.json"
    args = [py, "-I", EFF_TOOL, video, "--out", out, "--cache", video + ".eff.npz",
            "--groups", json.dumps({k: [int(v[0]), int(v[1])] for k, v in (groups or {}).items()}),
            "--hits", ",".join(str(int(h)) for h in hits or ()), "--exempt", _franges(exempt or ()),
            "--line", f"{eff_need(mc):.4f}", "--jmax", str(e["j_max"]), "--ppmax", str(int(e["pp_max"])),
            "--hwin", str(int(e["hwin"])), "--ppath", str(e["ppath"]),
            "--hwin-long", str(int(e.get("hwin_long", 48))), "--jlong", str(e.get("j_long_max", 0.5)),
            "--ret", json.dumps({k: e[k] for k in ("ret_win", "ret_n", "ret_frac") if k in e})]
    if not _run_tool("eff", args, e.get("timeout_s", 900)):
        return None
    return util.read_json(out)


def music_of(mj, base=None):
    """inputs/music.json → sync_stats 要的 music = {"file", "start_s", "map"}。music_map 没写就找配乐旁边的 <名>.music_map.json。"""
    if not mj or not mj.get("file"):
        return None
    f = mj["file"] if os.path.isabs(mj["file"]) else os.path.join(base or "", mj["file"])
    mp = mj.get("music_map")
    if mp and not os.path.isabs(mp):
        mp = os.path.join(os.path.dirname(f), os.path.basename(mp))
    if not mp:
        mp = os.path.splitext(f)[0] + ".music_map.json"
    return {"file": f, "start_s": float(mj.get("start_s") or 0.0), "map": mp if os.path.exists(mp) else None}


def sync_stats(video, music, beatmap, shotlist, py, own_audio=False, cfg=None):
    """实测卡点（p2/tools/syncmeas.py check，接口 2b）。music = {"file": 配乐绝对路径, "start_s": 片内第 0 帧对应的秒,
    "map": music_map 绝对路径}；beatmap / shotlist 传 dict（这里写临时 JSON 给工具）。own_audio=True 用视频自己的音轨
    （交付成片；music 有 file 时顺带做 sync_mux 互相关），否则配乐从 start_s 起切音轨（cut_rN 没声音）。cfg = 系列 gates.sync 覆盖。
    返回 {"checks": {...}, "issues": [...], "stats": {...}, "shots": {镜头: {n, heavy, frames, kinds}}}，同时存到 video + ".sync.json"；
    画面帧差缓存 video + ".sync.npz"。量不了返回 None，原因在 LAST_ERR["sync"]。"""
    if not _py_ok(py, "sync"):
        return None
    music, sc = music or {}, _sync_cfg(cfg)
    out = video + ".sync.json"
    args = [py, "-I", SYNC_TOOL, "check", video, "--out", out, "--cache", video + ".sync.npz",
            "--thr", json.dumps({k: v for k, v in sc.items() if k not in ("note", "calibration")}, ensure_ascii=False)]
    mp, mf = music.get("map"), music.get("file")
    if mp and os.path.exists(mp):
        args += ["--map", mp]
    elif (beatmap or {}).get("bpm"):
        args += ["--bpm", str(beatmap["bpm"])]                # 没有打击点表：节拍表的 BPM、第 0 帧在拍上（标签只有小节.拍.分）
    else:
        LAST_ERR["sync"] = "没有打击点表（music_map），节拍表里也没有 BPM"
        return None
    if mf and os.path.exists(mf):
        args += ["--music", mf, "--start-s", repr(float(music.get("start_s") or 0.0))]
    elif not own_audio:
        LAST_ERR["sync"] = f"配乐文件不存在：{mf}" if mf else "没给配乐文件（cut 没声音，要从配乐切音轨）"
        return None
    if own_audio:
        args.append("--own-audio")
    tmp = []
    try:
        for flag, obj, tag in (("--beatmap", beatmap, "sync-bm"), ("--shotlist", shotlist, "sync-sl")):
            if obj:
                tmp.append(_tmp_json(video, tag, obj))
                args += [flag, tmp[-1]]
        if not _run_tool("sync", args, sc.get("timeout_s", 900)):
            return None
    finally:
        for p in tmp:
            if os.path.exists(p):
                os.remove(p)
    return util.read_json(out)


def shot_sync(render, start_f, table, py, cfg=None):
    """逐镜卡点（p2/tools/syncmeas.py shot，接口 2c）：render = 单镜 render.mp4（第 0 帧 = 片内 start_f）；
    table = [{"F", "lvl": 重|轻|可, "attack_f", "desc"}]（片内帧号）。返回 {"ok", "misses", "silent", "text", …}，
    同时存到 render + ".sync.json"。量不了返回 None，原因在 LAST_ERR["shot"]。"""
    if not _py_ok(py, "shot"):
        return None
    sc, out = _sync_cfg(cfg), render + ".sync.json"
    tp = _tmp_json(render, "sync-table", list(table or []))
    try:
        ok = _run_tool("shot", [py, "-I", SYNC_TOOL, "shot", render, "--start-f", str(int(start_f)), "--table", tp, "--out", out,
                                "--thr", json.dumps({k: v for k, v in sc.items() if k not in ("note", "calibration")},
                                                    ensure_ascii=False)], sc.get("timeout_s", 900))
    finally:
        if os.path.exists(tp):
            os.remove(tp)
    return util.read_json(out) if ok else None


# ----------------------------------------------------------------------------- 逐帧量
def _median(xs):
    s = sorted(xs)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) if n else 0.0


def _color_stats(r, g, b, step=2):
    """一遍扫完：饱和像素（mx ≥ 64、色度 ≥ 0.35·mx）的 12 格色相直方图与有色占比（主色数用，口径不变）；
    外加整屏染色用的 36 格（每格 10°）色相直方图：带色像素（mx ≥ 40、色度 ≥ 0.12·mx）与其中的淡色（色度 < 0.25·mx）。"""
    bins, tint, pale = [0] * 12, [0] * 36, [0] * 36
    colored = total = 0
    for i in range(0, NPX, step):
        R, G, B = r[i], g[i], b[i]
        mx, mn = max(R, G, B), min(R, G, B)
        total += 1
        c = mx - mn
        if mx < 40 or c < 0.12 * mx:
            continue
        if mx == R:
            h = ((G - B) / c) % 6
        elif mx == G:
            h = (B - R) / c + 2
        else:
            h = (R - G) / c + 4
        k = int(h * 6) % 36
        tint[k] += 1
        if c < 0.25 * mx:
            pale[k] += 1
        if mx < 64 or c < 0.35 * mx:
            continue
        colored += 1
        bins[int(h * 2) % 12] += 1
    return bins, colored / max(1, total), tint, pale, total


def _hue_bins(r, g, b, step=2):
    """饱和像素的色相直方图（12 格，每格 30°）与有色像素占比。"""
    return _color_stats(r, g, b, step)[:2]


def _thumb(gs):
    """160×90 灰度 → 16×9 格（每格 10×10 的和）。"""
    rows = [[sum(gs[y * W + x:y * W + x + 10]) for x in range(0, W, 10)] for y in range(H)]
    return [sum(rows[by * 10 + yy][bx] for yy in range(10)) for by in range(9) for bx in range(16)]


def motion_series(video, size=(96, 54)):
    """镜内运动量用的逐帧帧差：96×54 灰度相邻帧平均绝对差（0–255），d[0] = 0（第 0 帧没有前一帧，统计时不算）。"""
    d, prev = [0.0], None
    for b in media.gray_frames(video, *size):
        if prev is not None:
            d.append(sum(map(abs, map(operator.sub, b, prev))) / len(b))
        prev = b
    return d


def _half(gs):
    """160×90 灰度（0..765）→ 80×45 灰度字节（0..255，2×2 平均）：切点检查用。"""
    return bytes((gs[i] + gs[i + 1] + gs[i + W] + gs[i + W + 1]) // 12
                 for y in range(0, H, 2) for i in range(y * W, y * W + W, 2))


def analyze(video, fps=60, sample_every=6, keep=(), motion_size=(96, 54)):
    """keep = 要留 80×45 灰度小图的帧号（切点 F±3），存在 win[帧号]。md = 镜内运动量的逐帧帧差（另解一遍 96×54 灰度）。"""
    d, corr, lum = [], [], []
    tone = []            # (帧号, p1, p99)
    hues = []            # (帧号, bins, 有色占比)
    tints = []           # (帧号, 36 格带色直方图, 36 格淡色直方图, 采样像素数)：整屏染色
    sthumbs = []         # (帧号, 16×9 灰度)：每个采样帧，相邻段落雷同用
    thumbs = {}          # 每秒第一帧的 16×9 灰度缩略
    keep, win = set(keep), {}
    first = last = None
    prev_b = prev_g = None
    prev_ss = prev_sum = 0
    for i, b in enumerate(media.rgb_frames(video, W, H)):
        r, g, bl = b[0::3], b[1::3], b[2::3]
        gs = [x + y + z for x, y, z in zip(r, g, bl)]          # 0..765
        s = sum(gs)
        ss = sum(map(operator.mul, gs, gs))
        if prev_b is None:
            d.append(0.0)
            corr.append(1.0)
            first = gs
        else:
            d.append(sum(map(abs, map(operator.sub, b, prev_b))) / (NPX * 3 * 255.0))
            sxy = sum(map(operator.mul, gs, prev_g)) - s * prev_sum / NPX
            vx, vy = ss - s * s / NPX, prev_ss - prev_sum * prev_sum / NPX
            corr.append(sxy / math.sqrt(vx * vy + 1e-9) if vx > 1e-6 and vy > 1e-6 else (1.0 if abs(s - prev_sum) < NPX else 0.0))
        lum.append(s / (NPX * 765.0))
        if i % sample_every == 0:
            srt = sorted(gs)
            tone.append((i, srt[int(0.01 * NPX)] / 765.0, srt[int(0.99 * NPX) - 1] / 765.0))
            hb, cf, tb, pb, tot = _color_stats(r, g, bl)
            hues.append((i, hb, cf))
            tints.append((i, tb, pb, tot))
            sthumbs.append((i, _thumb(gs)))
        if i % fps == 0:
            thumbs[i // fps] = [sum(gs[(by * 10 + yy) * W + bx * 10 + xx] for yy in range(10) for xx in range(10))
                                for by in range(9) for bx in range(16)]
        if i in keep:
            win[i] = _half(gs)
        last = gs
        prev_b, prev_g, prev_ss, prev_sum = b, gs, ss, s
    return {"d": d, "corr": corr, "lum": lum, "tone": tone, "hues": hues, "thumbs": thumbs,
            "first": first, "last": last, "fps": fps, "win": win, "tints": tints, "sthumbs": sthumbs,
            "md": motion_series(video, motion_size)}


def cuts_and_hits(a):
    d, corr = a["d"], a["corr"]
    n = len(d)
    cuts = [i for i in range(1, n) if corr[i] < 0.45 and d[i] > 0.035]
    cl = []
    for c in cuts:
        if not cl or c - cl[-1] >= 3:
            cl.append(c)
    pk = [i for i in range(1, n - 1)
          if d[i] >= max(2 * _median(d[max(1, i - 12):i + 13]), 0.03) and d[i] >= d[i - 1] and d[i] >= d[i + 1]]
    return cl, pk


def _corr(x, y):
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y))
    vx = sum((p - mx) ** 2 for p in x)
    vy = sum((q - my) ** 2 for q in y)
    return sxy / math.sqrt(vx * vy) if vx > 1e-9 and vy > 1e-9 else 1.0


def _t(f, fps):
    return round(f / fps, 2)


def _chk(cid, name, status, value=None, want=None, where=None, rule=""):
    """level 由 id 定（HARD 里的是 hard，其余 warn）；不是 hard 的检查不许 fail（降成 warn）。"""
    level = "hard" if cid in HARD else "warn"
    if status == "fail" and level != "hard":
        status = "warn"
    c = {"id": cid, "name": name, "status": status, "level": level, "value": value, "want": want, "rule": rule}
    if where:
        c["where"] = where[:20]
        if len(where) > 20:
            c["where_more"] = len(where) - 20
    return c


def _shot_of(f, shots):
    for s in shots or []:
        if s["start_f"] <= f < s["end_f"]:
            return s["id"]
    return None


# ----------------------------------------------------------------------------- 成片门禁
def video_checks(video, g, beatmap=None, shotlist=None, final=False, analysis=None, signature=None, py=None, music=None):
    """g = series.gates。返回 (checks, metrics)。signature = 签名色 hex（topic.signature_color.hex；不给就取全片最多的色相）。
    py = 引擎 Python（有效运动量、实测卡点在它的子进程里量）；music = {"file": 配乐, "start_s": 片内第 0 帧对应的秒,
    "map": music_map}（cut_rN 没声音：从配乐切音轨；交付成片用自己的音轨，有 file 时顺带做混流互相关）。
    镜内运动量、卡点、主角大字 / 数字重复：final=True（交付）判 fail，制作中途记 warn（照样给位置，返修看得到；逐镜数据在
    video + ".eff.json"、".sync.json"）。py 或 music 缺了：制作中途记 warn「没量」，交付记 fail。"""
    fps = g.get("fps", 60)
    out = []
    pr = media.probe(video)
    if not pr.get("ok"):
        return [_chk("spec_file", "成片文件", "fail", pr, "存在且有视频流", rule="交付规格")], {}
    out.append(_chk("spec_size", "分辨率", "ok" if (pr["width"], pr["height"]) == tuple(g["size"]) else "fail",
                    f"{pr['width']}×{pr['height']}", "×".join(map(str, g["size"])), rule="检查项·交付规格"))
    out.append(_chk("spec_fps", "帧率", "ok" if abs(pr["fps"] - fps) < 0.01 else "fail", pr["fps"], fps,
                    rule="检查项·交付规格"))
    out.append(_chk("spec_codec", "编码 / 视频流数", "ok" if pr["codec"] == "h264" and pr["video_streams"] == 1 else "fail",
                    f"{pr['codec']} ×{pr['video_streams']}", "h264 ×1", rule="检查项·交付文件数"))
    lo, hi = g["duration_s"]
    out.append(_chk("duration", "时长", "ok" if lo <= pr["duration_s"] <= hi else "warn", pr["duration_s"], f"{lo}–{hi} s",
                    rule="CHROMA·参数·时长"))
    if final:
        out.append(_chk("audio", "配乐音轨", "ok" if pr["audio"] else "fail", pr.get("audio_codec"), "有（一首配乐）",
                        rule="CHROMA·参数·音乐"))

    cc = g.get("cut_checks")
    keep = {j for F, _, _ in plan_cuts(shotlist) for j in range(F - cc["window"], F + cc["window"] + 1)} if cc else ()
    mc = _cfg(g, "motion")
    a = analysis or analyze(video, fps, keep=keep, motion_size=tuple(mc["size"]))
    if "md" not in a:
        a["md"] = motion_series(video, tuple(mc["size"]))
    n = len(a["d"])
    shots = (shotlist or {}).get("shots")
    if shotlist:
        want_n = shotlist["total_f"]
        out.append(_chk("frames", "帧数与镜头表一致", "ok" if n == want_n else "fail", n, want_n, rule="编排器"))
    dur = n / fps
    cuts, hits = cuts_and_hits(a)
    cps = len(cuts) / dur
    m = {"frames": n, "dur_s": round(dur, 3), "cuts": len(cuts), "cuts_per_s": round(cps, 2),
         "hits_per_s": round(len(hits) / dur, 2), "big_change_pct": round(100 * sum(1 for x in a["d"] if x > 0.08) / n, 1),
         "luma_jumps_per_s": round(sum(1 for i in range(1, n) if abs(a["lum"][i] - a["lum"][i - 1]) > 0.12) / dur, 2)}
    # v2 硬门禁：镜内运动量（11-04 起看有效运动量 + 原地摆动，引擎 Python 子进程量）、主角大字 / 数字不重复
    md_cut = motion_stats(a["md"], mc)["cut"]
    ex_m, _ = _motion_exempt(beatmap, len(a["md"]), md_cut, fps, mc)
    big_f = [int(h["F"]) for h in (beatmap or {}).get("hits") or [] if h.get("kind") in ("big", "drop", "final")]
    eff = eff_stats(video, {k: (s0, s1) for k, s0, s1 in _segs(shots, len(a["md"]), mc, fps)}, mc, py, big_f, ex_m)
    mk, mm = motion_checks(a["md"], mc, shots, beatmap, fps, final, eff=eff, eff_err=LAST_ERR.get("eff"))
    out += mk
    m["motion"] = mm
    if shots:
        out += repeat_checks(shots, _cfg(g, "repeat"), "fail" if final else "warn")[:1]   # 只报告那条计划门禁已经有了
    # 11-04 硬门禁：实测卡点（音轨现场重算起音；cut 没声音就从配乐 start_s 起切，交付成片用自己的音轨）
    own = bool(pr.get("audio")) and (final or not (music or {}).get("file"))
    sres = sync_stats(video, music, beatmap, shotlist, py, own, cfg=g.get("sync")) if music else None
    serr = LAST_ERR.get("sync") if music else "没给配乐 / 打击点表（music）"
    sk, sm = sync_checks(sres, _sync_cfg(g.get("sync")), final, own, serr, bool((music or {}).get("file")))
    out += sk
    m["sync"] = sm

    # 只奖励数量的密度门（11-04 起一律只报警，不管系列配置怎么写）：切点密度、第 1 秒构图数、重击密度
    cmin = _dens(g, "cuts_per_s_min")
    low = []
    for s0 in range(0, int(dur) - 1, 2):
        k = sum(1 for c in cuts if s0 * fps <= c < (s0 + 2) * fps)
        if k / 2 < cmin / 2:
            low.append(f"{s0}–{s0 + 2}s：{k / 2:.1f}/s")
    out.append(_chk("cut_density", "切点密度（只报警）", "ok" if cps >= cmin else "warn", round(cps, 2),
                    f"≥{cmin}/s（有忙有闲；低于一半的 2 秒窗口列在 where）", low, "检查项·节奏·切点密度"))
    # 第 1 秒构图数
    c1 = 1 + sum(1 for c in cuts if c < fps)
    fmin = _dens(g, "first_second_min")
    out.append(_chk("first_second", "第 1 秒构图数（只报警）", "ok" if c1 >= fmin else "warn", c1,
                    f"≥{fmin}", None, "检查项·节奏·第 1 秒构图数"))
    # 重击密度
    hps = len(hits) / dur
    lo_h, hi_h = _dens(g, "hits_per_s")
    busy = _dens(g, "hits_too_busy")
    st = "warn" if hps >= busy else ("ok" if lo_h <= hps <= hi_h else "warn")
    out.append(_chk("hits", "重击密度（只报警）", st, round(hps, 2), f"{lo_h}–{hi_h}/s；≥{busy} 报「太碎」", None,
                    "检查项·节奏·重击密度"))
    bad_st = "fail" if final else "warn"                       # 稳的底线（still、grey、colors、similar …）：制作中途记 warn，交付 fail
    # ≥1 s 静止段（屏息那一拍、最后一击之后的片尾海报不算：根「镜头」底线①只许读整句字、片尾海报、冷开场）
    ex_still, _ = _motion_exempt(beatmap, n, set(), fps, mc) if beatmap else (set(), [])
    eps, run, still = g["still_eps"], 0, []
    for i, x in enumerate(a["d"][1:], 1):
        run = run + 1 if (x < eps and i not in ex_still) else 0
        if run == fps:
            still.append(f"{_t(i - fps + 1, fps)}s 起（镜头 {_shot_of(i, shots)}）")
    out.append(_chk("still", "≥1 s 的静止段", "ok" if not still else bad_st, len(still), 0, still,
                    "根·镜头·底线①（全局 5）；屏息、片尾海报不算"))
    # 首尾同一张海报：最后一帧 → 第 0 帧这一跳不能是切点
    if a["first"] and a["last"]:
        dl = sum(abs(p - q) for p, q in zip(a["first"], a["last"])) / (NPX * 765.0)
        cl = _corr(a["first"], a["last"])
        seamless = not (cl < 0.45 and dl > 0.035)
        out.append(_chk("loop", "首尾循环（末帧接第 0 帧）", "ok" if seamless else "warn",
                        {"diff": round(dl, 4), "corr": round(cl, 3)}, "不像一个切点（corr ≥0.45 或 diff ≤0.035）", None,
                        "CHROMA·必须-1"))
    # 闪烁：只报告（待用户定 #7）
    per_s = {}
    for i in range(1, n):
        if abs(a["lum"][i] - a["lum"][i - 1]) > 0.12:
            per_s[i // fps] = per_s.get(i // fps, 0) + 1
    busy = [f"{s}s：{k} 次" for s, k in sorted(per_s.items()) if k >= 2]
    out.append(_chk("flicker", "整屏亮度翻转（只报告）", "info", m["luma_jumps_per_s"], "只报告", busy, "检查项·闪烁"))
    # 发灰：黑到不了黑、白到不了白
    grey = []
    for f, p1, p99 in a["tone"]:
        if p1 > g["grey_p1_max"] and p99 < g["grey_p99_min"]:
            grey.append(f)
    grey_w = _runs(grey, gap=g.get("tone_sample", 6) + 1, fps=fps, shots=shots, min_len=int(g.get("grey_min_s", 1.5) * fps))
    out.append(_chk("grey", "发灰（暗部>%.2f 且亮部<%.2f）" % (g["grey_p1_max"], g["grey_p99_min"]),
                    "ok" if not grey_w else bad_st, len(grey_w), f"连续 <{g.get('grey_min_s', 1.5)} s", grey_w, "根·配色·底线②（全局 10）"))
    # 主色数：按镜头（没有镜头表就按秒）
    out.append(_colors(a, g, shots, fps, bad_st))
    # 相似画面连续
    sims, runlen, start = [], 0, 0
    secs = sorted(a["thumbs"])
    for s0, s1 in zip(secs, secs[1:]):
        if _corr(a["thumbs"][s0], a["thumbs"][s1]) > 0.85:
            if runlen == 0:
                start = s0
            runlen += 1
            if runlen + 1 > g["similar_max_s"]:
                sims.append(f"{start}–{s1 + 1}s")
        else:
            runlen = 0
    out.append(_chk("similar", "相似画面连续（每秒缩略图 corr>0.85，粗查）", "ok" if not sims else bad_st, len(sims),
                    f"≤{g['similar_max_s']} s", sorted(set(sims)), "根·节奏·底线②（全局 8）"))
    # v2.1 规则树：开头第 1 秒有变化、结尾不留空黑屏（稳，交付硬）；切点两边都在动、镜内中位数（只报警）
    out += open_end_checks(a, mc, shots, fps, bad_st)
    if shotlist:
        out.append(cut_moving_check(a["md"], shotlist, mc, fps))
    imm = mc.get("in_median_min")
    if imm:
        v = mm.get("in_median")
        out.append(_chk("in_median", "整片镜内运动中位数（默认值）", "ok" if v is not None and v >= imm else "warn", v,
                        f"≥{imm:g}（{mc.get('in_median_note') or '本大类代表作 × 0.8'}；偏离写一句理由）", None, "类型卡·镜头·默认"))
    if final and pr.get("audio"):
        out.append(loudness_check(video, g.get("loudness") or {}))
    # v2 报警：整屏染色、相邻段落雷同
    tk, tm = tint_check(a, _cfg(g, "tint"), fps, shots, signature)
    out.append(tk)
    m["tint"] = tm
    sk, sm = section_check(a, _cfg(g, "sections"), fps, shotlist, beatmap)
    out.append(sk)
    m["sections"] = sm
    # 大卡点上有事件：11-04 起由实测卡点 sync_plan 量（拿真实起音对画面，不再拿计划对计划），big_hits 删掉
    if cc and shotlist:
        cks = cut_checks(a, shotlist, cc, fps)
        out += cks
        m.update({c["id"]: c["value"] for c in cks})
    return soften(out, g), m


def soften(checks, g):
    """系列 gates.soft = {检查 id: 理由}：这个系列的原片自己都过不了的稳的门禁
    在这个系列里降成报警：level 改 warn、fail 改 warn，理由记在 soft（报告、gates_tree 照样列出来；返修循环只当建议，不强制判不过）。
    工程底线（ENGINEERING）不收。两样都没写的系列（CHROMA）原样返回。"""
    soft = {k: v for k, v in ((g or {}).get("soft") or {}).items() if k not in ENGINEERING and not str(k).startswith("_")}
    off = {str(k) for k in (g or {}).get("off") or []} - ENGINEERING
    if not soft and not off:
        return checks
    for c in checks:
        if c.get("id") in off:
            c.update(status="skip", level="warn", off="系列 gates.off")
        elif c.get("id") in soft and c.get("level") == "hard":
            c["level"] = "warn"
            c["soft"] = str(soft[c["id"]])[:300]
            if c.get("status") == "fail":
                c["status"] = "warn"
    return checks


# ----------------------------------------------------------------------------- 切点（看像素，不看声明；全部只报警）
HW, HH = W // 2, H // 2
_DARK = bytes(1 if v < 20 else 0 for v in range(256))
_BRIGHT = bytes(1 if v > 235 else 0 for v in range(256))


def plan_cuts(shotlist):
    """镜头表里的每个切点 (F, 镜头 id, 声明)：镜头之间的带下一镜的 transition_in（dict），镜头内的声明是 None。"""
    out = []
    for i, s in enumerate((shotlist or {}).get("shots") or []):
        if i:
            t = s.get("transition_in")
            out.append((s["start_f"], s["id"], t if isinstance(t, dict) else {"why": str(t or "")}))
        out += [(c, s["id"], None) for c in s.get("cuts_f", [])]
    return sorted(out, key=lambda x: x[0])


def _blend(x, a, b, cc):
    """x ≈ α·a + (1−α)·b 吗？返回 (α, 残差 / 两帧差)；a、b 差得不够大（不像跨了切点）时返回 (None, None)。
    叠化、跨镜头混帧、经过白 / 黑的淡入淡出都会命中（基线经过白的淡变：残差 0.01）。"""
    d = [p - q for p, q in zip(a, b)]
    dd = sum(v * v for v in d)
    if dd <= 0 or sum(map(abs, d)) / (len(d) * 255.0) < cc["blend_min_diff"]:
        return None, None
    xb = [p - q for p, q in zip(x, b)]
    al = sum(p * q for p, q in zip(xb, d)) / dd
    return al, math.sqrt(sum((p - al * q) ** 2 for p, q in zip(xb, d)) / dd)


def _edges(gb):
    """80×45 灰度 → 16×9 格边缘能量（相邻像素差）+ 能量重心（1080p 像素）。"""
    cells = [0.0] * 144
    for y in range(HH - 1):
        r, cy = y * HW, min(8, y // 5) * 16
        for x in range(HW - 1):
            i = r + x
            cells[cy + min(15, x // 5)] += abs(gb[i + 1] - gb[i]) + abs(gb[i + HW] - gb[i])
    tot = sum(cells) or 1.0
    return cells, (sum(v * (k % 16 + 0.5) for k, v in enumerate(cells)) / tot * 120.0,
                   sum(v * (k // 16 + 0.5) for k, v in enumerate(cells)) / tot * 120.0)


def cut_checks(a, shotlist, cc, fps):
    """按镜头表每个切点 F 看 F-w…F+w：cut_dip 闪黑闪白、cut_blend 叠化 / 跨镜头混帧、cut_carry 对位类两侧连续性。只报警。"""
    k, win, n = cc["window"], a.get("win") or {}, len(a["d"])
    dip, blend, carry = [], [], []
    seen = nb = 0
    lo, hi = cc["blend_alpha"]
    for F, sid, t in plan_cuts(shotlist):
        fr = [win.get(j) for j in range(F - k, F + k + 1)]
        if F - k < 0 or F + k >= n or any(x is None for x in fr):
            continue
        seen += 1
        where = f"{sid} {'入点' if t is not None else '镜内'} {F}（{_t(F, fps)}s" + \
                (f"，{t.get('kind') or '没写 kind'}）" if t is not None else "）")
        for tab, name in ((_DARK, "闪黑"), (_BRIGHT, "闪白")):
            fs = [x.translate(tab).count(1) / len(x) for x in fr]
            j = max(range(len(fs)), key=fs.__getitem__)
            if fs[j] >= cc["dip_frac"] and fs[0] < cc["dip_bounce"] and fs[-1] < cc["dip_bounce"]:
                dip.append(f"{where}：{name}（第 {F - k + j} 帧整屏 {100 * fs[j]:.0f}% 近{name[1]}，前后都不是）")
                break
        found = None
        for j in range(1, 2 * k):                       # 只看跨过切点的一对（j-s 在切点前、j+s 在切点后）：镜头内的亮度渐变不算
            for s_ in range(max(1, j - k + 1, k - j), min(j, 2 * k - j) + 1):
                al, r = _blend(fr[j], fr[j - s_], fr[j + s_], cc)
                if al is not None and lo <= al <= hi and r < cc["blend_resid"]:
                    found = (F - k + j, al, r)
                    break
            if found:
                break
        if found:
            blend.append(f"{where}：第 {found[0]} 帧像前后两帧按 {found[1]:.2f} 混合（残差 {found[2]:.2f}）")
        if t is not None and t.get("kind") in cc["carry_kinds"]:
            nb += 1
            (e0, c0), (e1, c1) = _edges(fr[k - 1]), _edges(fr[k])
            ec, sh = _corr(e0, e1), math.hypot(c0[0] - c1[0], c0[1] - c1[1])
            if ec < cc["carry_corr_min"] and sh > cc["carry_shift_px"]:
                carry.append(f"{where}：两侧边缘相关 {ec:.2f}、主体重心偏 {sh:.0f}px（声明的 eye：{t.get('eye') or '没写'}）")
    rule = "全局 6（看像素，不看声明）"
    return [_chk("cut_dip", f"切点闪黑闪白（看了 {seen} 个切点的 F±{k}）", "ok" if not dip else "warn", len(dip), 0, dip, rule),
            _chk("cut_blend", "切点叠化 / 跨镜头混帧", "ok" if not blend else "warn", len(blend), 0, blend, rule),
            _chk("cut_carry", "对位类切点两侧连续（%s）" % "、".join(cc["carry_kinds"]), "ok" if not carry else "warn",
                 f"{nb - len(carry)}/{nb}", f"边缘相关 ≥{cc['carry_corr_min']} 或重心偏 ≤{cc['carry_shift_px']}px", carry, rule)]


# ----------------------------------------------------------------------------- v2.1 规则树接进来的几条（lib/tree.py 的 VIDEO）
def open_end_checks(a, mc, shots, fps, bad_status="warn"):
    """根「开头结尾」底线：①第 1 秒有变化（前 60 帧里 ≥2 个切点，或镜内帧差均值 ≥ 每镜门槛；静止标题卡、黑底上慢慢淡入都不算）；
    ②结尾不留空黑屏（最后一帧不是 ≥95% 近黑）。Light、点火都过（第 1 秒 8、9 个构图；片尾停在海报上）。"""
    d = a["md"]
    st = motion_stats(d, mc)
    refs = mc["ref_mean"]
    need = mc["shot_min_ratio"] * (min(refs.values()) if mc.get("ref_pick", "min") == "min" else sum(refs.values()) / len(refs))
    m1 = min(len(d), fps)
    ncut = sum(1 for i in range(1, m1) if i in st["cut"])
    ins = [d[i] for i in range(1, m1) if i not in st["cut"]]
    mean = sum(ins) / len(ins) if ins else 0.0
    ok1 = ncut >= 2 or mean >= need
    c1 = _chk("first_motion", "第 1 秒有变化", "ok" if ok1 else bad_status, f"切点 {ncut} 个、镜内帧差均值 {mean:.2f}",
              f"≥2 个切点或镜内均值 ≥{need:.2f}（静止标题卡不算）", [] if ok1 else [f"0–1s（{_shot_of(0, shots) or '片头'}）"],
              "根·开头结尾·底线①")
    last = a.get("last") or []
    dark = sum(1 for v in last if v < 60) / len(last) if last else 0.0
    c2 = _chk("end_black", "结尾不留空黑屏", "ok" if dark < 0.95 else bad_status, f"最后一帧近黑 {100 * dark:.0f}%", "<95%",
              [] if dark < 0.95 else [f"最后一帧（{_shot_of(len(d) - 1, shots) or '片尾'}）"], "根·开头结尾·底线②")
    return [c1, c2]


def cut_moving_check(d, shotlist, mc, fps, k=3):
    """根「转场」底线④：镜头之间的切点两边都在动——切点前 k 帧、后 k 帧的镜内帧差均值都 ≥ still_eps（上一镜不等停稳再切，
    下一镜不从静止起步）。没在定稿片上校过（定稿片没有对得上的镜头表），只报警、进返修问题单。"""
    bad, seen = [], 0
    n = len(d)
    for F, sid, t in plan_cuts(shotlist):
        if t is None or F - k < 1 or F + k >= n:
            continue
        seen += 1
        pre = sum(d[F - k:F]) / k                 # d[i] = 第 i-1 → i 帧；d[F] 是切点本身，不算
        post = sum(d[F + 1:F + k + 1]) / k
        if pre < mc["still_eps"] or post < mc["still_eps"]:
            side = "前后都" if pre < mc["still_eps"] and post < mc["still_eps"] else ("切点前" if pre < mc["still_eps"] else "切点后")
            bad.append(f"{sid} 入点 {F}（{_t(F, fps)}s）：{side}静止（前 {pre:.2f}、后 {post:.2f}）")
    return _chk("cut_moving", f"切点两边都在动（看了 {seen} 个镜头之间的切点）", "ok" if not bad else "warn", len(bad), 0, bad,
                "根·转场·底线④")


def loudness_check(video, lc):
    """根「配乐」底线③：响度 −14 LUFS（±1）、真峰 ≤ −1 dBTP（ffmpeg ebur128）。Light −14.0、点火 −13.9；
    试跑稿 v1 −4.2（没做响度标准化，v2.1 第 6 环节补上了 loudnorm）。"""
    target, tol, tp_max = lc.get("target_lufs", -14.0), lc.get("tol_lu", 1.0), lc.get("true_peak_max", -1.0)
    m = media.loudness(video)
    if m.get("I") is None:
        return _chk("loudness", "响度", "fail", m.get("error", "量不出来"), f"{target:g} LUFS ±{tol:g}，真峰 ≤{tp_max:g} dBTP",
                    None, "根·配乐·底线③")
    ok = abs(m["I"] - target) <= tol and (m.get("TP") is None or m["TP"] <= tp_max + 0.05)
    return _chk("loudness", "响度", "ok" if ok else "fail", f"{m['I']:.1f} LUFS，真峰 {m.get('TP', '?')} dBTP",
                f"{target:g} LUFS ±{tol:g}，真峰 ≤{tp_max:g} dBTP", None, "根·配乐·底线③")


# ----------------------------------------------------------------------------- 镜内运动量（硬门禁，「最关键去 PPT 感」）
def motion_stats(d, mc):
    """96×54 灰度帧差的口径：全片帧差中位 → 切点阈值；切点帧以外是镜内。返回切点帧集合和镜内均值 / 中位 / 近乎静止占比。"""
    xs = d[1:]
    med = _median(xs)
    thr = max(mc["cut_abs"], mc["cut_k"] * (med + 1))
    cut = {i for i in range(1, len(d)) if d[i] > thr}
    ins = [d[i] for i in range(1, len(d)) if i not in cut]
    still = sum(1 for x in ins if x < mc["still_eps"])
    return {"cut": cut, "thr": thr, "median_all": med, "mean": sum(ins) / len(ins) if ins else 0.0,
            "median": _median(ins), "still_frac": still / len(ins) if ins else 1.0, "n_in": len(ins)}


def _motion_exempt(beatmap, n, cut, fps, mc):
    """不算进每镜均值的帧：屏息那一拍、最后一击之后的海报停留（原片这两处本来就静）。
    没有节拍表时：片尾最后一个切点之后（≤ tail_max_s 秒）当海报停留。"""
    ex, why = set(), []
    if beatmap:
        beat = int(round(beatmap.get("beat_f") or 0))
        for h in beatmap.get("hits") or []:
            if h.get("kind") == "breath" and beat:
                ex |= set(range(h["F"], min(n, h["F"] + beat)))
                why.append(f"屏息 {h['F']}–{h['F'] + beat}")
            if h.get("kind") == "final":
                ex |= set(range(h["F"], n))
                why.append(f"最后一击 {h['F']} 之后")
    elif cut:
        last = max(cut)
        if n - last <= mc["tail_max_s"] * fps:
            ex |= set(range(last, n))
            why.append(f"最后一个切点 {last} 之后")
    return ex, why


def _segs(shots, n, mc, fps):
    """每镜的段：有镜头表按镜头，没有就每 window_f 帧一段（Light 这类没有对得上的镜头表的原片）。"""
    return [(s["id"], s["start_f"], s["end_f"]) for s in shots] if shots else \
        [(f"{a / fps:.1f}s", a, min(n, a + mc["window_f"])) for a in range(0, n, mc["window_f"])]


def eff_checks(eff, mc, final, err=None, troughs=()):
    """有效运动量三行（「拿抽搐、轻微摆动代替动效」）：motion_eff（每镜够不够）、motion_sway（原地摆动充数）硬，
    motion_content（静图 + 推拉嫌疑）只报警。eff = eff_stats 的结果；没量出来（py 缺、工具出错）：制作中途 warn，交付 fail。
    troughs = 计划里的低谷镜头（trough_shots）：线放低到 trough_ratio 倍。"""
    E, need = _eff_cfg(mc), eff_need(mc)
    bad = "fail" if final else "warn"
    tag = "" if final else "（交付时是硬门禁）"
    rule = "「拿抽搐、轻微摆动代替动效」；有效运动量（p2/tools/effmotion.py：分块平移，一拍里原地来回的部分不算）"
    ref_txt = "、".join(f"{k} {v:g}" for k, v in E["ref_eff"].items())
    want_e = (f"每镜 ≥{need:.2f}（原片整片有效量 {ref_txt} 里最低的 × {E['ratio']:g}；一拍里原地来回的部分不算）"
              + (f"；计划里的低谷镜头 {'、'.join(sorted(troughs))} ≥{need * float(E.get('trough_ratio', 1.0)):.2f}" if troughs else ""))
    want_s = (f"原地来回占比 ≤{100 * E['j_max']:.0f}%（原片 100 帧窗最高 36%）、同一张图宽紧来回跳 <{E['pp_max']} 次、"
              f"两拍窗口原地来回 ≤{100 * E.get('j_long_max', 0.5):.0f}%（慢摆 / 漂浮）、回到旧构图 100 帧内 <{E.get('ret_win', 6)} 次（原片最多 3）")
    want_c = f"非刚性内容 ≥{E['content_warn']:g}（原片 100 帧窗最低 2.99）"
    if not eff:
        why = f"没量（{err or '没给引擎 Python'}）"
        return [_chk("motion_eff", "每镜有效运动量" + tag, bad, why, want_e, [], rule),
                _chk("motion_sway", "原地摆动充数" + tag, bad, why, want_s, [], rule),
                _chk("motion_content", "静图 + 推拉嫌疑（非刚性内容，只报警）", "skip", why, want_c, [], rule)]
    shots = {k: v for k, v in (eff.get("shots") or {}).items() if v}
    low = [f"{k}：有效运动量 {v['eff']:.2f}（帧差 {v['d96']:.2f}，原地来回 {100 * v['J']:.0f}%）" + ("（低谷镜头）" if k in troughs else "")
           for k, v in shots.items() if v["eff"] < shot_need(mc, k, troughs)]
    sway = [sway_text(k, v, E) for k, v in shots.items() if not sway_ok(v, E)]
    cont = [f"{k}：非刚性内容 {v['content']:.2f}（有效运动量 {v['eff']:.2f}）" for k, v in shots.items() if v["content"] < E["content_warn"]]
    sway_bad = bad if E.get("sway_hard", True) else "warn"
    return [_chk("motion_eff", "每镜有效运动量" + tag, bad if low else "ok", f"{len(shots) - len(low)}/{len(shots)} 镜够",
                 want_e, low, rule),
            _chk("motion_sway", "原地摆动充数" + tag, sway_bad if sway else "ok", f"{len(sway)} 镜", want_s, sway, rule),
            _chk("motion_content", "静图 + 推拉嫌疑（非刚性内容，只报警）", "warn" if cont else "ok", f"{len(cont)} 镜", want_c, cont, rule)]


def sway_ok(v, E):
    """原地摆动充数：工具判了（ok_sway，含 J_long、回到旧构图）就用工具的；旧结果只有 J / 乒乓。"""
    if "ok_sway" in v and "sway_why" in v:
        return bool(v["ok_sway"])
    return v["J"] <= E["j_max"] and v["pingpong"] < E["pp_max"]


def sway_text(k, v, E):
    why = v.get("sway_why") or []
    parts = [f"原地来回占 {100 * v['J']:.0f}%"]
    if v.get("pingpong", 0) >= E["pp_max"]:
        parts.append(f"同一张图宽紧来回跳 {v['pingpong']} 次")
    if "J_long" in why:
        parts.append(f"两拍窗口里原地来回占 {100 * v['J_long']:.0f}%（慢摆 / 漂浮）")
    if "returns" in why:
        parts.append(f"切了又回到这一镜前面的构图 {v['returns']} 次（100 帧内最多 {v.get('returns_win')} 次，"
                     f"第 {', '.join(map(str, (v.get('returns_at') or [])[:6]))} 帧…）")
    return f"{k}：" + "，".join(parts)


def motion_checks(d, mc, shots, beatmap, fps, final, eff=None, eff_err=None):
    """镜内帧差（motion_shot，11-04 起只报告）、整片近乎静止帧 ≤ 12%（motion_still，硬；final=False 时记 warn），
    加上有效运动量三行（eff_checks；eff 是 eff_stats 的结果）。没有镜头表就按 window_f 帧一段量。"""
    st = motion_stats(d, mc)
    n, cut = len(d), st["cut"]
    refs = mc["ref_mean"]
    ref = min(refs.values()) if mc.get("ref_pick", "min") == "min" else sum(refs.values()) / len(refs)
    need = mc["shot_min_ratio"] * ref
    ex, ex_why = _motion_exempt(beatmap, n, cut, fps, mc)
    segs = _segs(shots, n, mc, fps)
    bad, per, stills = [], {}, []
    for sid, a0, a1 in segs:
        idx = [i for i in range(max(1, a0 + 1), min(n, a1)) if i not in cut]
        v = [d[i] for i in idx if i not in ex]
        k_still = sum(1 for i in idx if d[i] < mc["still_eps"])
        if k_still:
            stills.append((k_still, f"{sid}（{_t(a0, fps)}–{_t(a1, fps)}s）：近乎静止 {k_still} 帧 / 镜内 {len(idx)} 帧"))
        if len(v) < mc["min_frames"]:
            continue
        mean = sum(v) / len(v)
        per[sid] = round(mean, 2)
        if mean < need:
            bad.append(f"{sid}（{_t(a0, fps)}–{_t(a1, fps)}s）：镜内帧差均值 {mean:.2f}，近乎静止 {k_still} 帧")
    bad_status = "fail" if final else "warn"
    tag = "" if final else "（交付时是硬门禁）"
    ref_txt = "、".join(f"{k} {v:g}" for k, v in refs.items())
    rule = " 去 PPT 感；96×54 灰度帧差量法（切点帧除外）"
    # 帧差这条只报告（原地来回摆和真往前走量出来一样，拿它当线只会逼代理凑帧差）；硬门禁是下面的有效运动量
    c1 = _chk("motion_shot", "每镜镜内帧差（只报告；硬门禁看有效运动量）", "info" if bad else "ok",
              f"{len(per) - len(bad)}/{len(per)} 镜 ≥ 旧线",
              f"旧线：每镜镜内帧差均值 ≥ {need:.2f}（原片 {ref_txt} 里取{'低的' if mc.get('ref_pick', 'min') == 'min' else '平均'} × "
              f"{mc['shot_min_ratio']:g}；不算{('、'.join(ex_why)) or '切点帧'}）", bad, rule)
    c2 = _chk("motion_still", "整片镜内近乎静止帧" + tag, bad_status if st["still_frac"] > mc["still_frac_max"] else "ok",
              f"{100 * st['still_frac']:.1f}%", f"≤{100 * mc['still_frac_max']:g}%（帧差 < {mc['still_eps']:g}；Light 10.5%、点火 3.2%）",
              [w for _, w in sorted(stills, key=lambda x: -x[0])], rule)
    mm = {"cuts": len(cut), "cut_thr": round(st["thr"], 2), "in_mean": round(st["mean"], 2), "in_median": round(st["median"], 2),
          "still_pct": round(100 * st["still_frac"], 1), "shot_need": round(need, 2), "per_shot": per}
    if eff:
        mm["eff"] = {"line": round(eff_need(mc), 3), "film": eff.get("film"),
                     "per_shot": {k: ({x: v.get(x) for x in ("eff", "d96", "J", "J_long", "pingpong", "returns", "returns_win",
                                                              "luma_osc", "content", "ok_eff", "ok_sway", "sway_why")} if v else None)
                                  for k, v in (eff.get("shots") or {}).items()}}
    return [c1, c2] + eff_checks(eff, mc, final, eff_err, trough_shots(beatmap, {"shots": shots or []})), mm


# ----------------------------------------------------------------------------- 实测卡点（硬门禁，「卡点卡得不准」）
SYNC_ROWS = (("sync_grid", "卡点网格（节拍表 / 音轨和音乐对得上）"), ("sync_top", "最强重音画面卡上"),
             ("sync_select", "卡点有选择（重音上切，不是到处切）"), ("sync_plan", "计划大卡点卡上"),
             ("sync_silent", "无声重击（只报警）"), ("sync_on", "默认卡点集合 ON%（只报告）"), ("sync_mux", "成片音轨和配乐对齐（互相关）"),
             ("sync_cuts", "计划切点真切出来（不是同一张图挪一点）"))


def _sync_value(cid, v):
    if cid == "sync_grid" and isinstance(v, dict):
        s = (f"起音 − 格点中位 {v.get('attack_ms_med'):+.1f} ms（前后半漂移 {v.get('drift_ms', 0):+.1f} ms：{v.get('drift_src', '起音 − 格点')}）"
             + (f"，现场起音对打击点表 {v['map_dev_ms']:+.1f} ms" if v.get("map_dev_ms") is not None else "")) \
            if v.get("attack_ms_med") is not None else ""
        if v.get("drop_attack_f") is not None:
            s += f"，DROP 起音 {v['drop_attack_f']}（{v.get('drop_pos')}）" + \
                 (f" 对{v.get('drop_anchor')} {v['drop_anchor_f']}" if v.get("drop_anchor_f") is not None else "")
        return s or v
    if cid == "sync_on" and isinstance(v, dict):
        return f"ON {v.get('ON')}/{v.get('n')}（{100 * (v.get('on_pct') or 0):.0f}%），晚 {v.get('LATE')}、早 {v.get('EARLY')}、弱 {v.get('WEAK')}"
    if cid == "sync_mux" and isinstance(v, list):
        return "；".join(f"{o['t']:g}s 偏移 {o['lag_ms']:+.3f} ms、相关 {o['corr']:.3f}" for o in v) or "量不出来"
    return v


def sync_checks(res, sc, final, own_audio=False, err=None, has_music=True):
    """sync_stats 的结果 → 门禁行（id 照 SYNC_ROWS）。工具自己按阈值判 ok / fail，这里在制作中途（final=False）把 fail 降成 warn；
    sync_silent 只报警、sync_on 只报告、sync_mux 只在用成片自己的音轨时有（没有配乐文件对不了就 skip）。
    res = None（py / 配乐 / 打击点表缺了，或工具出错）：硬的几行制作中途 warn「没量」、交付 fail。返回 (行, 指标)。"""
    bad = "fail" if final else "warn"
    tag = "" if final else "（交付时是硬门禁）"
    rule = "「卡点卡得不准」；sync_check 量法（160×90 帧差，判 ON 的锚 = 真实起音后第一帧，音轨现场重算）"
    ids = [x for x in SYNC_ROWS if x[0] != "sync_mux" or own_audio]
    if res is None:
        why = f"没量（{err or '没给引擎 Python / 配乐'}）"
        rows = []
        for cid, name in ids:
            st = bad if cid in HARD else ("skip" if cid == "sync_on" else "warn")
            rows.append(_chk(cid, name + (tag if cid in HARD else ""), st, why, "引擎 Python + p2/tools/syncmeas.py + 配乐 / 打击点表", [], rule))
        return rows, {"measured": False, "why": why}
    ck, rows = res.get("checks") or {}, []
    for cid, name in ids:
        c = ck.get(cid)
        if c is None:
            if cid == "sync_mux":
                rows.append(_chk(cid, name, "skip" if not has_music else bad, "没有配乐文件，互相关做不了" if not has_music else "没量",
                                 f"每段 |偏移| ≤{sc['mux_lag_ms']:g} ms、相关 ≥{sc['mux_corr']:g}", [], rule))
            continue
        st = c.get("status", "skip")
        if st == "fail":
            st = bad
        name_ = name if cid != "sync_top" else f"最强 {sc['top_n']} 个重音画面卡上"
        rows.append(_chk(cid, name_ + (tag if cid in HARD else ""), st, _sync_value(cid, c.get("value")), c.get("want"),
                         c.get("where") or [], rule))
    sm = dict(res.get("stats") or {}, measured=True, issues=len(res.get("issues") or []),
              by_shot={k: {"n": v["n"], "heavy": v["heavy"]} for k, v in (res.get("shots") or {}).items()})
    return rows, sm


# ----------------------------------------------------------------------------- 重复（镜头表；主角大字、数字是硬门禁）
_NUM = re.compile(r"(?<![\w.])([-−–]?)(\d+(?:\.\d+)?)(?:\s*(°\s*[CFKcfk]?|%|[A-Za-z]{1,3}(?![A-Za-z])))?")


def _numbers(text):
    """文字里的数 → {(值, 单位)}；值统一成 -273.15 这种写法，单位大写、去掉 °（°C → C，82° → 空）。"""
    out = set()
    for sign, v, unit in _NUM.findall(str(text or "")):
        x = float(v)
        val = ("-" if sign and x else "") + f"{x:g}"
        out.add((val, (unit or "").replace("°", "").replace(" ", "").upper()))
    return out


def _word(text):
    """主角大字归一：大写、合并空白、去掉首尾标点；去掉数字和单位后没有字母的（纯数字大字）返回空，交给数字那条查。"""
    w = re.sub(r"\s+", " ", str(text or "").strip().upper()).strip(" .,!?;:'\"“”‘’·-—")
    rest = _NUM.sub("", w)
    return w if re.search(r"[A-Z一-鿿]", rest) else ""


def _places(items_by_shot, bookend):
    """{项: [镜头下标…]} → 出现在几个地方；首尾两镜都有的（同一张海报）算一个地方。"""
    out = {}
    last = len(items_by_shot) - 1
    for i, items in enumerate(items_by_shot):
        for it in items:
            out.setdefault(it, [])
            if i not in out[it]:
                out[it].append(i)
    cnt = {}
    for it, idx in out.items():
        k = len(idx)
        if bookend and last > 0 and 0 in idx and last in idx:
            k -= 1
        cnt[it] = k
    return out, cnt


def _num_groups(nums_by_shot, bookend):
    """数值相同、单位相同或有一边没写单位的算同一个数。返回 [(显示名, [镜头下标…], 地方数, 数值)]。"""
    by_val = {}
    for i, ns in enumerate(nums_by_shot):
        for val, unit in ns:
            by_val.setdefault(val, []).append((i, unit))
    out, last = [], len(nums_by_shot) - 1
    for val, occ in by_val.items():
        units = sorted({u for _, u in occ if u})
        groups = [[u] for u in units] or [[""]]
        for gi, gu in enumerate(groups):
            idx = sorted({i for i, u in occ if u in gu or not u or (not units)})
            k = len(idx) - (1 if bookend and last > 0 and 0 in idx and last in idx else 0)
            u = {"C": "°C", "F": "°F"}.get(gu[0], gu[0])
            out.append((val + (" " + u if u else ""), idx, k, val))
    return out


def repeat_checks(shots, rc, bad_status="fail", prefix=""):
    """镜头表里的重复：repeat 主角大字 hero_word、数字（hero_word + words）各只出现在一个镜头（硬）；
    repeat_more words 里的其他大词、小标签 label 里的数（只报告）。计划门禁 prefix="p_"、bad_status="fail"（发回导演重做）；
    成片门禁制作中途 warn、交付 fail。"""
    ids = [s["id"] for s in shots]
    bookend = rc.get("bookend", True)
    heroes = [{_word(s.get("hero_word"))} - {""} for s in shots]
    nums = [set().union(*[_numbers(t) for t in [s.get("hero_word", "")] + list(s.get("words") or [])]) for s in shots]
    where = []
    occ, cnt = _places(heroes, bookend)
    for w, k in sorted(cnt.items(), key=lambda x: occ[x[0]][0]):
        if k > 1:
            where.append(f"主角大字 {w}：{'、'.join(ids[i] for i in occ[w])}")
    nbad = 0
    for name, idx, k, _ in sorted(_num_groups(nums, bookend), key=lambda x: x[1][0]):
        if k > 1:
            nbad += 1
            where.append(f"数字 {name}：{'、'.join(ids[i] for i in idx)}")
    wbad = len(where) - nbad
    name = "主角大字、数字全片不重复" + ("" if bad_status == "fail" else "（交付时是硬门禁）")
    c1 = _chk(prefix + "repeat", name, bad_status if where else "ok", f"大字重复 {wbad}、数字重复 {nbad}",
              "各 0（首尾同一张海报算一次）", where, "「字不能重复」；去 PPT 感·诊断 1、6")
    # 只报告：words 里的其他大词、小标签里的数
    more = []
    words = [{_word(w) for w in [s.get("hero_word", "")] + list(s.get("words") or [])} - {""} for s in shots]
    occ2, cnt2 = _places(words, bookend)
    hero_rep = {w for w, k in cnt.items() if k > 1}
    for w, k in sorted(cnt2.items(), key=lambda x: occ2[x[0]][0]):
        if k > 1 and w not in hero_rep:
            more.append(f"大词 {w}：{'、'.join(ids[i] for i in occ2[w])}")
    labels = [_numbers(s.get("label", "")) | nums[i] for i, s in enumerate(shots)]
    num_rep = {v for _, _, k, v in _num_groups(nums, bookend) if k > 1}
    for nm, idx, k, v in sorted(_num_groups(labels, bookend), key=lambda x: x[1][0]):
        if k > 1 and v not in num_rep:
            more.append(f"数字（含小标签）{nm}：{'、'.join(ids[i] for i in idx)}")
    c2 = _chk(prefix + "repeat_more", "其他大词、小标签里的数字重复（只报告）", "info", len(more), "只报告", more,
              "「字不能重复」")
    return [c1, c2]


# ----------------------------------------------------------------------------- 整屏染色、相邻段落雷同（只报警）
def _hex_hue(hx):
    """'#5FF2FF' → 色相（度）；灰色返回 None。"""
    try:
        R, G, B = (int(str(hx).lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))
    except ValueError:
        return None
    mx, mn = max(R, G, B), min(R, G, B)
    c = mx - mn
    if not c:
        return None
    h = ((G - B) / c) % 6 if mx == R else ((B - R) / c + 2 if mx == G else (R - G) / c + 4)
    return h * 60.0


def tint_check(a, tc, fps, shots, signature=None):
    """签名色像素占全屏 > frame_frac 的帧（整屏签名色）、其中淡色就 > frame_frac 的帧（滤镜式）各占采样帧多少。"""
    rows = a.get("tints") or []
    if not rows:
        return _chk("tint", "整屏染色（签名色铺满全屏）", "skip", None, None, rule="去 PPT 感·诊断 3"), {}
    hue = _hex_hue(signature) if signature else None
    src = "topic" if hue is not None else "自动（全片最多的色相）"
    if hue is None:
        agg = [sum(r[1][k] for r in rows) for k in range(36)]
        k0 = max(range(36), key=agg.__getitem__)
        hue = k0 * 10 + 5.0
    k0 = int(hue // 10) % 36
    tol = max(0, int(round(tc["hue_tol_deg"] / 10.0)))
    near = [(k0 + o) % 36 for o in range(-tol, tol + 1)]
    full, filt = [], []
    for f, tb, pb, tot in rows:
        if sum(tb[k] for k in near) / max(1, tot) > tc["frame_frac"]:
            full.append(f)
        if sum(pb[k] for k in near) / max(1, tot) > tc["frame_frac"]:
            filt.append(f)
    n = len(rows)
    sf, sp = len(full) / n, len(filt) / n
    gap = (rows[1][0] - rows[0][0] + 1) if n > 1 else 7
    where = []
    if sp > tc["pale_share_max"]:
        where += ["滤镜式 " + w for w in _runs(filt, gap, fps, shots, 0)]
    if sf > tc["share_max"]:
        where += ["整屏签名色 " + w for w in _runs(full, gap, fps, shots, int(0.5 * fps))]
    bad = sf > tc["share_max"] or sp > tc["pale_share_max"]
    m = {"hue": round(hue), "hue_src": src, "full_pct": round(100 * sf, 1), "pale_pct": round(100 * sp, 1)}
    return _chk("tint", "整屏染色（签名色铺满全屏 / 淡色当滤镜）", "warn" if bad else "ok",
                f"整屏签名色 {100 * sf:.1f}%，滤镜式 {100 * sp:.1f}% 的帧（签名色相 {hue:.0f}°，{src}）",
                f"整屏签名色 ≤{100 * tc['share_max']:g}%、滤镜式 ≤{100 * tc['pale_share_max']:g}%（单帧：签名色像素 > "
                f"{100 * tc['frame_frac']:g}% 全屏）", where, "去 PPT 感·诊断 3「颜色当成了滤镜」"), m


def _sections(shotlist, beatmap, n, fps, win_s):
    shots = (shotlist or {}).get("shots") or []
    if shots and all(s.get("section") for s in shots):
        out = []
        for s in shots:
            if out and out[-1][0] == s["section"]:
                out[-1][2] = s["end_f"]
            else:
                out.append([s["section"], s["start_f"], s["end_f"]])
        return out, "镜头表"
    secs = (beatmap or {}).get("sections") or []
    if secs and all("start_f" in x and "end_f" in x for x in secs):
        return [[x["id"], x["start_f"], x["end_f"]] for x in secs], "节拍表"
    w = max(1, int(win_s * fps))
    return [[f"{a / fps:.0f}s", a, min(n, a + w)] for a in range(0, n, w)], f"每 {win_s:g} s"


def _stat(x):
    k = len(x)
    mu = sum(x) / k
    return mu, sum((p - mu) ** 2 for p in x) / k


def section_check(a, sc, fps, shotlist=None, beatmap=None):
    """相邻两段：一段的采样帧在另一段里有近似帧（16×9 灰度 corr ≥ frame_corr、亮度差 < 10%）的占比，两边平均；再看主色直方图交集。"""
    n = len(a["d"])
    secs, src = _sections(shotlist, beatmap, n, fps, sc["win_s"])
    full = 76500.0                                     # 16×9 一格 = 10×10 像素 × 0..765
    flat = (0.02 * full) ** 2                          # 方差 < (2% 亮度)²：没结构的平帧，不比结构
    th = [(f, t, _stat(t)) for f, t in a.get("sthumbs") or []]
    feats = []
    for sid, a0, a1 in secs:
        fr = [x for x in th if a0 <= x[0] < a1 and x[2][1] > flat]
        hb = [0] * 12
        cf = []
        for f, b, c in a["hues"]:
            if a0 <= f < a1:
                hb = [p + q for p, q in zip(hb, b)]
                cf.append(c)
        tot = sum(hb) or 1
        feats.append((sid, fr, [v / tot for v in hb], sum(cf) / len(cf) if cf else 0.0))

    def dup(X, Y):
        hit = 0
        for _, x, (mx, vx) in X:
            for _, y, (my, vy) in Y:
                if abs(mx - my) < 0.1 * full and \
                        sum((p - mx) * (q - my) for p, q in zip(x, y)) / (len(x) * math.sqrt(vx * vy)) >= sc["frame_corr"]:
                    hit += 1
                    break
        return hit / len(X)

    pairs, where = {}, []
    for (ia, fa, ha, ca), (ib, fb, hb_, cb) in zip(feats, feats[1:]):
        if len(fa) < 3 or len(fb) < 3:
            continue
        dp = (dup(fa, fb) + dup(fb, fa)) / 2
        col = sum(map(min, ha, hb_)) if ca >= 0.03 and cb >= 0.03 else (1.0 if ca < 0.03 and cb < 0.03 else 0.0)
        pairs[f"{ia}-{ib}"] = {"dup": round(dp, 2), "color": round(col, 2)}
        if dp >= sc["dup_min"] and col >= sc["color_min"]:
            where.append(f"{ia} 和 {ib}：{100 * dp:.0f}% 的帧在另一段有近似构图，主色交集 {col:.2f}")
    mx = max((p["dup"] for p in pairs.values()), default=0.0)
    return _chk("section_similar", f"相邻段落雷同（按{src}分段，粗查）", "warn" if where else "ok",
                f"{len(where)} 对（近似帧占比最高 {mx:.2f}）",
                f"近似帧占比 <{sc['dup_min']:g} 或主色交集 <{sc['color_min']:g}", where,
                "全局 8；去 PPT 感·诊断 6"), {"by": src, "pairs": pairs}


def _runs(frames, gap, fps, shots, min_len):
    out, s, p = [], None, None
    for f in frames + [None]:
        if f is not None and p is not None and f - p <= gap:
            p = f
            continue
        if s is not None and p - s >= min_len:
            out.append(f"{_t(s, fps)}–{_t(p, fps)}s（{_shot_of(s, shots)}）")
        s = p = f
    return out


def _colors(a, g, shots, fps, bad_status="warn"):
    groups = {}
    for f, bins, cf in a["hues"]:
        key = _shot_of(f, shots) or f"{f // fps}s"
        acc = groups.setdefault(key, [[0] * 12, 0.0, 0])
        acc[0] = [x + y for x, y in zip(acc[0], bins)]
        acc[1] += cf
        acc[2] += 1
    bad = []
    for key, (bins, cf, k) in groups.items():
        tot = sum(bins)
        if not tot or cf / k < 0.03:
            continue
        on = [b / tot >= g.get("colors_bin_min", 0.2) for b in bins]
        nhue = sum(1 for i in range(12) if on[i] and not on[i - 1])
        if all(on):
            nhue = 1
        if nhue > g["colors_max"]:
            bad.append(f"{key}：{nhue} 个主色（有色像素 {100 * cf / k:.0f}%）")
    return _chk("colors", "一段的主色数（不算黑白，粗查）", "ok" if not bad else bad_status, len(bad), f"≤{g['colors_max']}", bad,
                "根·配色·底线①（全局 10）")


# ----------------------------------------------------------------------------- 冻结区
def freeze_record(md5s, shots, ids):
    return {s["id"]: {"start_f": s["start_f"], "end_f": s["end_f"],
                      "md5": util.sha256_bytes("".join(md5s[s["start_f"]:s["end_f"]]).encode())}
            for s in shots if s["id"] in ids}


def freeze_checks(md5s, frozen: dict):
    bad = []
    for sid, r in frozen.items():
        now_ = util.sha256_bytes("".join(md5s[r["start_f"]:r["end_f"]]).encode())
        if now_ != r["md5"]:
            bad.append(f"{sid}（帧 {r['start_f']}–{r['end_f']}）")
    return _chk("frozen", "冻结镜头逐帧不变", "ok" if not bad else "fail", f"{len(frozen) - len(bad)}/{len(frozen)}",
                "全部不变", bad, "编排器·冻结区")


# ----------------------------------------------------------------------------- 计划门禁（导演出表后、开工前）
def plan_checks(beatmap, shotlist, series):
    """返回检查列表。fail = 工程底线（时长、铺满全片、屏息 + DROP 结构、零件名）和主角大字 / 数字重复，作为导演环节的不合格原因发回重做；
    warn = 规则（只提醒、不拦），发回提醒一次，改不改都收，记进 gates_plan.json 和 report.md 的报警。"""
    g, fps = series["gates"], series["gates"]["fps"]
    out = []
    total = shotlist["total_f"]
    dur = total / fps
    lo, hi = g["duration_s"]
    out.append(_chk("p_duration", "计划时长", "ok" if lo <= dur <= hi else "fail", round(dur, 2), f"{lo}–{hi} s",
                    rule="CHROMA·参数·时长"))
    shots = shotlist["shots"]
    gaps = [f"{a['id']}→{b['id']}" for a, b in zip(shots, shots[1:]) if a["end_f"] != b["start_f"]]
    if shots and (shots[0]["start_f"] != 0 or shots[-1]["end_f"] != total):
        gaps.append("没有从第 0 帧铺到片尾")
    out.append(_chk("p_cover", "镜头首尾相接铺满全片", "ok" if not gaps else "fail", len(gaps), 0, gaps, "编排器"))
    cuts = sorted(set(c for s in shots for c in s["cuts_f"]) | set(s["start_f"] for s in shots[1:]))
    cps = len(cuts) / dur
    pmin, p1min = _dens(g, "plan_cuts_per_s_min"), _dens(g, "plan_first_second_min")      # 只奖励数量：只报警
    out.append(_chk("p_cut_density", "计划切点密度（只报警）", "ok" if cps >= pmin else "warn", round(cps, 2),
                    f"≥{pmin}/s", rule="CHROMA·必须-4"))
    c1 = 1 + sum(1 for c in cuts if c < fps)
    out.append(_chk("p_first_second", "计划第 1 秒构图数（只报警）", "ok" if c1 >= p1min else "warn", c1,
                    f"≥{p1min}", rule="CHROMA·必须-2"))
    br = [h for h in beatmap["hits"] if h["kind"] == "breath"]
    dr = [h for h in beatmap["hits"] if h["kind"] == "drop"]
    ok = len(br) == 1 and len(dr) == 1 and br[0]["F"] < dr[0]["F"]
    out.append(_chk("p_breath", "高潮前唯一一次屏息 + 一个 DROP", "ok" if ok else "fail",
                    f"屏息 {len(br)}，DROP {len(dr)}", "各 1，屏息在前", rule="CHROMA·参数·节奏"))
    if dr:
        F, beat = dr[0]["F"], beatmap["beat_f"]
        held = [c for c in cuts if F < c < F + beat - 1]
        out.append(_chk("p_drop_hold", "DROP 后立住一拍再切", "ok" if not held else "warn", len(held), 0,
                        [f"帧 {c}" for c in held], "CHROMA·参数·节奏"))
    fin = [h for h in beatmap["hits"] if h["kind"] == "final"]
    ok = len(fin) == 1 and total - fin[0]["F"] <= beatmap["bar_f"] * 1.01
    out.append(_chk("p_final", "最后一击在最后一小节内、在拍上", "ok" if ok else "warn",
                    fin[0]["F"] if fin else None, "最后一小节", rule="CHROMA·参数·结尾"))
    texts = []
    for s in shots:
        for t in [s.get("hero_word", ""), s.get("label", "")] + list(s.get("words") or []):
            if not util.ascii_only(t):
                texts.append(f"{s['id']}：{t}")
    lang = str((series.get("text") or {}).get("lang") or "en").strip().lower()      # 屏上字语言（系列 text.lang；CHROMA = en）
    if "zh" in lang:
        out.append(_chk("p_english", f"屏上字语言（系列 text.lang = {lang}，中英都收）", "info", len(texts), "—", texts,
                        f"{series.get('name', '系列')}·参数·字体"))
    else:
        out.append(_chk("p_english", "屏上字全英文", "ok" if not texts else "warn", len(texts), 0, texts, "CHROMA·参数·字体"))
    imgs = {}
    for s in shots:
        imgs.setdefault(s.get("image", ""), []).append(s["id"])
    rep = [f"{k}：{v}" for k, v in imgs.items() if k and len(v) > g["same_image_max"]]
    out.append(_chk("p_image", "同一意象全片 ≤%d 次" % g["same_image_max"], "ok" if not rep else "warn", len(rep), 0, rep, "全局 8"))
    run, bad = 0, []
    for a_, b_ in zip(shots, shots[1:] + [None]):
        run += a_["end_f"] - a_["start_f"]
        if b_ is None or b_.get("look") != a_.get("look"):
            if run / fps > g["similar_max_s"]:
                bad.append(f"{a_.get('look')} 连续 {run / fps:.1f}s（到 {a_['id']}）")
            run = 0
    out.append(_chk("p_alternate", "设计帧和实拍交替（同类连续 ≤%ds）" % g["similar_max_s"], "ok" if not bad else "warn",
                    len(bad), 0, bad, "CHROMA·参数·画面；全局 8"))
    small = [f"{s['id']}：{s.get('label_px')}px" for s in shots if s.get("label_px") and s["label_px"] < g["label_px_min"]]
    out.append(_chk("p_label", "小标签字号 ≥%dpx" % g["label_px_min"], "ok" if not small else "warn", len(small), 0, small,
                    "检查项·字·字号"))
    parts = set(series["parts"]) | {a for v in series["parts"].values() for a in v.get("alias") or []}   # 中文别名也认（叠层、碎裂）
    unk = [f"{s['id']}：{p}" for s in shots for p in s.get("parts", []) if p not in parts]
    out.append(_chk("p_parts", "零件都在系列卡清单里", "ok" if not unk else "fail", len(unk), 0, unk, "系列卡·零件"))
    no_ev = [s["id"] for s in shots if len((s.get("event") or "").strip()) < 4]
    out.append(_chk("p_event", "每镜有一个动画事件", "ok" if not no_ev else "warn", len(no_ev), 0, no_ev, "全局 5"))
    out += repeat_checks(shots, _cfg(g, "repeat"), "fail", "p_")        # 硬：主角大字、数字重复发回导演重做
    out += carry_checks(shots, series.get("transitions"))
    return soften(out, g)


def playbook_slugs(path):
    """转场手册里出现的 slug 集合；读不到返回 None。"""
    try:
        return set(re.findall(r"（`([a-z0-9-]+)`", util.read(path)))
    except OSError:
        return None


def carry_checks(shots, tr):
    """转场声明（只报警）：首镜以外每镜 kind 在枚举里、eye ≥4 字、有 why、不写闪黑闪白淡入淡出叠化、slug 在 playbook 里；
    拍上硬切的占比照系列参数（CHROMA 不设上限，只报告）。"""
    if not tr:
        return []
    kinds, ban = tr["kinds"], [w.lower() for w in tr.get("banned", [])]
    slugs = playbook_slugs(tr["playbook"]) if tr.get("playbook") else None
    bad, hard = [], 0
    for s in shots[1:]:
        t = s.get("transition_in")
        t = t if isinstance(t, dict) else {"why": str(t or "")}
        k = (t.get("kind") or "").strip()
        why = []
        if not k:
            why.append("没写 kind")
        elif k not in kinds:
            why.append(f"kind「{k}」不在枚举里")
        if len((t.get("eye") or "").strip()) < 4:
            why.append("eye 没写清（眼睛跟着什么过切点）")
        if not (t.get("why") or "").strip():
            why.append("没写 why")
        txt = " ".join(str(v) for v in t.values()).lower()
        why += [f"写了「{w}」（全局 6 不收）" for w in ban if w in txt]
        if t.get("slug") and slugs is not None and t["slug"] not in slugs:
            why.append(f"slug「{t['slug']}」不在转场条目里")
        if why:
            bad.append(f"{s['id']}：" + "，".join(why))
        hard += k == "拍上硬切"
    n = max(1, len(shots) - 1)
    mx = tr.get("hard_cut_share_max")
    over = mx is not None and hard / n > mx
    return [_chk("p_carry", "转场声明完整（kind / eye / why）", "ok" if not bad else "warn", len(bad), 0, bad,
                 "全局 6"),
            _chk("p_hard_cut", "镜头之间拍上硬切的占比", "warn" if over else "info", f"{hard}/{n} = {100 * hard / n:.0f}%",
                 "不设上限（只报告）" if mx is None else f"≤{100 * mx:.0f}%", None, "系列参数 transitions.hard_cut_share_max")]


def summarize(checks):
    n = {"ok": 0, "warn": 0, "fail": 0, "info": 0, "skip": 0}
    for c in checks:
        n[c["status"]] = n.get(c["status"], 0) + 1
    return n


def fmt(n):
    return f"通过 {n['ok']} · 报警 {n['warn']} · 不过 {n['fail']} · 只报告 {n['info']}"


def verdict(checks):
    """交付放不放行：只看 hard 的 fail；其余报警照样交付。"""
    hard = [c for c in checks if c["status"] == "fail" and c.get("level", "hard" if c["id"] in HARD else "warn") == "hard"]
    return {"deliver": not hard, "hard_fail": [c["id"] for c in hard],
            "hard_ok": [c["id"] for c in checks if c.get("level") == "hard" and c["status"] == "ok"],
            "warn": [c["id"] for c in checks if c["status"] == "warn"]}


def table(checks):
    mark = {"ok": "通过", "warn": "报警", "fail": "不过", "info": "报告", "skip": "跳过"}
    lines = []
    for c in checks:
        w = "；".join(c.get("where") or [])
        hard = "［硬］" if c.get("level") == "hard" else "　　　"
        lines.append(f"{mark[c['status']]:4s}{hard}{c['name']}: {c['value']}（要求 {c['want']}）" + (f"  位置：{w[:200]}" if w else ""))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="成片门禁：硬门禁（规格 / 帧数 / 冻结区 / 有效运动量 / 实测卡点 / 大字数字重复）挡交付，其余只报警")
    ap.add_argument("video")
    ap.add_argument("--series", default="chroma")
    ap.add_argument("--beatmap")
    ap.add_argument("--shotlist")
    ap.add_argument("--topic", help="topic.json：取签名色（整屏染色用）")
    ap.add_argument("--signature", help="签名色 hex，例如 '#5FF2FF'（比 --topic 优先）")
    ap.add_argument("--final", action="store_true", help="交付前：多查配乐音轨、混流；镜内运动量、卡点、重复判 fail")
    ap.add_argument("--py", help="引擎 Python（有 numpy / cv2 / librosa；默认取系列 engine.python）")
    ap.add_argument("--music", help="inputs/music.json（配乐 file、start_s、music_map）：实测卡点用")
    ap.add_argument("--out")
    a = ap.parse_args()
    series = util.read_json(os.path.join(util.HERE, "series", a.series + ".json"))
    bm = util.read_json(a.beatmap) if a.beatmap else None
    sl = util.read_json(a.shotlist) if a.shotlist else None
    sig = a.signature or (((util.read_json(a.topic) or {}).get("signature_color") or {}).get("hex") if a.topic else None)
    py = a.py or (os.path.join(util.PARENT, series["engine"]["python"]) if (series.get("engine") or {}).get("python") else None)
    mu = music_of(util.read_json(a.music), os.path.dirname(os.path.abspath(a.music))) if a.music else None
    checks, m = video_checks(a.video, series["gates"], bm, sl, a.final, signature=sig, py=py, music=mu)
    print(table(checks))
    v = verdict(checks)
    print("交付：" + ("放行" if v["deliver"] else "不放行，硬门禁不过 " + "、".join(v["hard_fail"])))
    print("指标：" + json.dumps(m, ensure_ascii=False))
    if a.out:
        util.write_json(a.out, {"video": a.video, "metrics": m, "summary": summarize(checks), "verdict": v, "checks": checks})
    return 0 if v["deliver"] else 1


if __name__ == "__main__":
    sys.exit(main())
