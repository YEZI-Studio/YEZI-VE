# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
"""ffmpeg 小工具：探测、抽帧、局部放大、3×3 联系表、切线图、逐帧哈希、逐帧小图（RGB / 灰度）、拼接、配乐对齐（片尾淡出）、mock 占位视频。只用 ffmpeg/ffprobe。"""
from __future__ import annotations

import json
import os
import subprocess

from . import util

FONT = next((p for p in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                         "/Library/Fonts/Arial.ttf") if os.path.exists(p)), None)
ENV = {"PATH": util.BIN_PATH, "HOME": util.HOME}
# 占位视频与拼接用同一套编码参数（concat 直接拷流，冻结区逐帧不变）
H264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-g", "60", "-bf", "0",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def ff(args, log_path=None, timeout=1800):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-y", "-v", "error"] + args,
                       capture_output=True, text=True, env=ENV, timeout=timeout)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("$ ffmpeg " + " ".join(args)[:2000] + "\n" + r.stderr[-4000:] + "\n")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败（rc {r.returncode}）：{r.stderr.strip()[-600:]}")
    return r


def probe(path):
    if not os.path.exists(path):
        return {"exists": False}
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,nb_frames,pix_fmt",
                        "-of", "json", path], capture_output=True, text=True, env=ENV, timeout=120)
    d = json.loads(r.stdout or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        return {"exists": True, "ok": False, "error": "没有视频流"}
    n, _, dd = (v.get("r_frame_rate") or "0/1").partition("/")
    fps = float(n) / float(dd or 1) if float(dd or 1) else 0
    return {"exists": True, "ok": True, "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "fps": round(fps, 3), "nb_frames": int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else None,
            "duration_s": round(float((d.get("format") or {}).get("duration") or 0), 3), "pix_fmt": v.get("pix_fmt"),
            "audio": bool(a), "audio_codec": a.get("codec_name") if a else None,
            "video_streams": sum(1 for s in d.get("streams", []) if s.get("codec_type") == "video")}


def _txt(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def _label(text, size=54, y="h-th-40"):
    if not FONT:
        return ""
    return (f",drawtext=fontfile='{FONT}':text='{_txt(text)}':fontsize={size}:fontcolor=white:"
            f"box=1:boxcolor=black@0.6:boxborderw=12:x=(w-tw)/2:y={y}")


def placeholder_shot(out, frames, cuts_local, label, hue, fps=60, log_path=None):
    """mock 渲染：testsrc2 横向滚动（每帧 2% 画宽，镜内帧差均值约 9，和原片 10–12 一个量级，过得了镜内运动量门禁）+ 每个切点在原图和反相图之间切换
    （灰度相关≈-1，切点检测量得到）；标签写镜头号和版本。"""
    k = "+".join(f"gte(n,{c})" for c in cuts_local if 0 < c < frames) or "0"
    fc = (f"testsrc2=s=1920x1080:r={fps}:d={frames / fps:.6f},hue=h={hue},scroll=horizontal=0.02,split[a][b];"
          f"[b]negate,vflip[b2];[a][b2]overlay=0:0:enable='mod({k},2)',"
          f"drawbox=x=0:y=0:w=iw:h=ih*0.04:color=black:t=fill,drawbox=x=0:y=ih*0.96:w=iw:h=ih*0.04:color=white:t=fill"
          + _label(label) + "[v]")            # 上下各一条纯黑、纯白（v2.1：「不发灰」是交付硬门禁，占位片要黑得到黑、白得到白）
    ff(["-filter_complex", fc, "-map", "[v]", "-frames:v", str(frames), "-r", str(fps)] + H264 + [out],
       log_path=log_path)


def placeholder_still(out, label, hue, log_path=None):
    fc = f"testsrc2=s=1920x1080:r=60:d=1,hue=h={hue}" + _label(label, 72, "(h-th)/2") + "[v]"
    ff(["-filter_complex", fc, "-map", "[v]", "-frames:v", "1", out], log_path=log_path)


def concat(files, out, log_path=None):
    lst = out + ".txt"
    util.write(lst, "".join(f"file '{os.path.abspath(f)}'\n" for f in files))
    ff(["-f", "concat", "-safe", "0", "-i", lst, "-map", "0:v", "-c", "copy", out], log_path=log_path)
    os.remove(lst)


def frames_jpg(video, idx, out_dir, prefix, width=1280, crop=None, log_path=None):
    """按帧号批量抽 JPEG（一次解码）。crop=(x,y,w,h) 在 1080p 坐标里取局部再放大到 width。返回路径列表。"""
    os.makedirs(out_dir, exist_ok=True)
    idx = sorted(set(int(i) for i in idx))
    sel = "+".join(f"eq(n\\,{i})" for i in idx)
    vf = f"select='{sel}'"
    if crop:
        x, y, w, h = crop
        vf += f",crop={w}:{h}:{x}:{y}"
    vf += f",scale={width}:-2:flags=lanczos"
    pat = os.path.join(out_dir, f"{prefix}_%03d.jpg")
    ff(["-i", video, "-vf", vf, "-vsync", "0", "-q:v", "3", pat], log_path=log_path)
    outs = []
    for j, i in enumerate(idx, 1):
        src, dst = pat % j, os.path.join(out_dir, f"{prefix}_f{i:05d}.jpg")
        os.replace(src, dst)
        outs.append(dst)
    return outs


def sheet(video, idx, out, cols=3, cell_w=640, fps=60, log_path=None, max_n=9, f0=None):
    """联系表：最多 max_n 格（默认 9 = 3×3），每格左上角印时间；f0 = 这条视频第 0 帧的片内帧号时改印「F 片内帧号」
    （单镜 render.mp4 给代理自己看的 ctx/look/self/）。"""
    idx = sorted(set(int(i) for i in idx))[:max_n]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    rows = (len(idx) + cols - 1) // cols
    sel = "+".join(f"eq(n\\,{i})" for i in idx)
    vf = f"select='{sel}',scale={cell_w}:-2"
    if FONT:
        txt = "%{pts\\:hms}" if f0 is None else f"F%{{eif\\:round(t*{fps})+{int(f0)}\\:d}}"
        vf += (f",drawtext=fontfile='{FONT}':text='{txt}':fontsize=26:fontcolor=white:box=1:"
               f"boxcolor=black@0.6:x=8:y=8")
    vf += f",tile={cols}x{rows}:nb_frames={len(idx)}:padding=4:color=gray"
    ff(["-i", video, "-vf", vf, "-vsync", "0", "-frames:v", "1", "-q:v", "3", out], log_path=log_path)
    return out


def image_sheet(paths, out, cols=3, cell_w=480, log_path=None):
    """图按给的顺序，最多 cols × 3 张。"""
    paths = list(paths)[:cols * 3]
    if not paths:
        raise RuntimeError("没有图")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    rows = (len(paths) + cols - 1) // cols
    args = []
    for p in paths:
        args += ["-i", p]
    flt = "".join(f"[{i}:v]scale={cell_w}:-2,setsar=1,format=rgb24[v{i}];" for i in range(len(paths)))
    flt += "".join(f"[v{i}]" for i in range(len(paths))) + f"concat=n={len(paths)}:v=1:a=0,tile={cols}x{rows}:padding=4:color=gray"
    ff(args + ["-filter_complex", flt, "-frames:v", "1", "-q:v", "3", out], log_path=log_path)
    return out


def kymo(video, a, b, out, y=0.5, w=960, log_path=None, max_rows=400):
    """切线图（审片看「原地摆动」用，纯 ffmpeg）：[a, b) 每帧取第 y×高 那一行（2 px）拉成 w×3，竖着拼起来，往下是时间。
    斜纹 / 展开 = 推进，竖直线 = 静止，锯齿 / 波浪 = 原地来回摆。帧太多（> max_rows）就隔帧取，图不超过 3×max_rows 高。"""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    k = max(1, -(-(b - a) // max_rows))
    n = max(1, -(-(b - a) // k))
    sel = f"between(n\\,{a}\\,{b - 1})" + (f"*not(mod(n-{a}\\,{k}))" if k > 1 else "")
    ff(["-i", video, "-vf", f"select='{sel}',crop=iw:2:0:ih*{y},scale={w}:3:flags=neighbor,tile=1x{n}",
        "-frames:v", "1", "-update", "1", "-q:v", "3", out], log_path=log_path)
    return out


def tool_sheet(tool, video, out_png, n=12, log_path=None):
    """yezi-ve/tools/sheet.sh：全片均匀 n 帧联系表 PNG，再转一张 JPEG 发给模型。返回 JPEG 路径。"""
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    r = subprocess.run(["bash", tool, video, out_png, str(n)], capture_output=True, text=True, env=ENV, timeout=600)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"$ bash {tool} {video} {out_png} {n}\n{r.stdout[-400:]}{r.stderr[-1000:]}\n")
    if r.returncode != 0 or not os.path.exists(out_png):
        raise RuntimeError(f"sheet.sh 失败（rc {r.returncode}）：{r.stderr.strip()[-400:]}")
    jpg = os.path.splitext(out_png)[0] + ".jpg"
    ff(["-i", out_png, "-q:v", "3", jpg], log_path=log_path)
    return jpg


def framemd5(video):
    """每帧解码后的 MD5（只看视频流）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", video, "-map", "0:v:0",
                        "-f", "framemd5", "-"], capture_output=True, text=True, env=ENV, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"framemd5 失败：{r.stderr[-400:]}")
    return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]


def rgb_frames(video, w=160, h=90):
    """逐帧下采样 RGB24 字节（给门禁量运动、切点、亮度）。"""
    n = w * h * 3
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", video, "-map", "0:v:0",
                          "-vf", f"scale={w}:{h}:flags=area,format=rgb24", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, env=ENV)
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield b
    finally:
        p.stdout.close()
        p.wait()


def gray_frames(video, w=96, h=54):
    """逐帧下采样灰度字节（镜内运动量门禁用）。滤镜串照 96×54 灰度帧差的量法（scale=96:54,format=gray），
    数字才能和那张表（Light 12.52 / 点火 10.44 / 试跑稿 v1 5.82）直接比。"""
    n = w * h
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-i", video, "-map", "0:v:0",
                          "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE, env=ENV)
    try:
        while True:
            b = p.stdout.read(n)
            if len(b) < n:
                break
            yield b
    finally:
        p.stdout.close()
        p.wait()


def click_track(out, bpm, dur_s, downbeat_s=0.0, log_path=None):
    """mock 配乐：每拍一声、每小节第一拍高音的节拍器。"""
    beat = 60.0 / bpm
    ph = f"mod(t-{downbeat_s:.4f}+{beat * 64:.4f},{beat:.6f})"
    bar = f"mod(t-{downbeat_s:.4f}+{beat * 64:.4f},{beat * 4:.6f})"
    expr = (f"0.5*sin(2*PI*1500*t)*lt({bar},0.04)+0.35*sin(2*PI*900*t)*lt({ph},0.03)*gte({bar},0.04)"
            f"+0.4*sin(2*PI*55*t)*exp(-30*{ph})")
    ff(["-f", "lavfi", "-i", f"aevalsrc='{expr}':s=48000:d={dur_s:.4f}", "-ac", "2", out], log_path=log_path)


LOUD = {"I": -14.0, "TP": -1.5, "LRA": 11.0}     # 根「配乐」底线③：−14 LUFS、真峰 ≤ −1 dBTP（AAC 编码会抬一点真峰，目标留 0.5 dB）


def _ff_info(args, timeout=600):
    """要 ffmpeg 打到 stderr 的统计（loudnorm 的 JSON、ebur128 的 Summary 都在 info 级别）。"""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-v", "info"] + args,
                       capture_output=True, text=True, env=ENV, timeout=timeout)
    return r.returncode, r.stderr


def loudness(path):
    """ebur128 量整合响度 I（LUFS）和真峰 TP（dBTP）。返回 {I, TP} 或 {error}。"""
    try:
        rc, err = _ff_info(["-i", path, "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"])
    except Exception as e:  # noqa: BLE001
        return {"I": None, "error": str(e)[:200]}
    tail = err[err.rfind("Summary:"):] if "Summary:" in err else ""
    import re
    mi = re.search(r"I:\s*(-?[\d.]+|-inf)\s*LUFS", tail)
    mp = re.search(r"True peak:\s*Peak:\s*(-?[\d.]+|-inf)", tail.replace("\n", " "))
    if not mi:
        return {"I": None, "error": f"ebur128 没出结果（rc {rc}）"}
    f = lambda x: float("-inf") if x == "-inf" else float(x)  # noqa: E731
    return {"I": f(mi.group(1)), "TP": round(f(mp.group(1)), 2) if mp else None}


def _loudnorm_filter(audio, audio_start, dur, tgt):
    """两遍 loudnorm 的第一遍：量这一段，返回第二遍用的滤镜（linear=true：整段只调增益，不压动态、不改编曲）。量不出来返回 None。"""
    base = f"loudnorm=I={tgt['I']}:TP={tgt['TP']}:LRA={tgt['LRA']}"
    try:
        rc, err = _ff_info(["-ss", f"{audio_start:.4f}", "-t", f"{dur:.4f}", "-i", audio, "-af", base + ":print_format=json",
                            "-f", "null", "-"])
        js = json.loads(err[err.rfind("{"):err.rfind("}") + 1])
        return (base + f":measured_I={js['input_i']}:measured_TP={js['input_tp']}:measured_LRA={js['input_lra']}"
                f":measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    except Exception:  # noqa: BLE001
        return None


FADE_OUT_S = 0.25          # 片尾音频淡出（满电平硬切接回第 0 帧会「啪」一下；循环播放时首尾不断）


def mux(video, audio, out, audio_start=0.0, log_path=None, loud=None, fade_out=FADE_OUT_S):
    """视频直接拷流 + 一首配乐从 audio_start 秒起（不拼接、不变速），AAC 320k。
    loud = {I, TP, LRA}：把这一段配乐整段调到目标响度（两遍 loudnorm，linear 只改增益）；None 不调。
    fade_out = 片尾音频淡出秒数（滤镜链最后一节 afade；0 不淡）。"""
    pr = probe(video)
    dur = pr.get("duration_s") or 0.0
    flt = []
    if loud:
        x = _loudnorm_filter(audio, audio_start, max(1.0, dur or 30.0), loud)
        if x:
            flt.append(x)
    if fade_out and dur > 2 * fade_out:
        flt.append(f"afade=t=out:st={dur - fade_out:.4f}:d={fade_out:.4f}")
    af = ["-af", ",".join(flt), "-ar", "48000"] if flt else []
    ff(["-i", video, "-ss", f"{audio_start:.4f}", "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"] + af +
       ["-c:a", "aac", "-b:a", "320k", "-shortest", "-movflags", "+faststart", out], log_path=log_path)
