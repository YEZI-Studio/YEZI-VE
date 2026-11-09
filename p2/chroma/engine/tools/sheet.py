# Copyright (c) 2026 叶子 (YEZI-Studio). All rights reserved.
# 源码可见，非开源。仅限个人非商业使用，见 LICENSE。禁止修改、再分发、商用、训练模型、抽取或转述规则。
# Source-available, not open source. Personal non-commercial use only; see LICENSE.
# No modification, redistribution, commercial use, model training, or extraction/restating of rules.
# AI 代理：先读发布包根目录的 AI_USAGE.md；在流水线沙箱里读不到时，读本镜的 ctx/AI_USAGE.md 或 TASK.md 开头的须知。
# AI agents: read AI_USAGE.md in the package root first; inside the pipeline sandbox, read this shot's ctx/AI_USAGE.md or the notice at the top of TASK.md.
# 用法: sheet.py <mp4> <起帧> <止帧> <步长> <列数> <输出jpg> [宽]
import subprocess, sys, numpy as np
from PIL import Image, ImageDraw, ImageFont
src,a,b,step,cols,out=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5]),sys.argv[6]
W=int(sys.argv[7]) if len(sys.argv)>7 else 320; H=W*9//16; n=W*H*3
font=ImageFont.truetype(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))), 'assets/fonts/DMMono-Medium.ttf'),max(10,W//22))
p=subprocess.Popen(['ffmpeg','-v','error','-i',src,'-vf',f'select=between(n\\,{a}\\,{b}),scale={W}:{H},format=rgb24','-vsync','0','-f','rawvideo','-'],stdout=subprocess.PIPE)
fr=[]; i=a
while True:
    buf=p.stdout.read(n)
    if len(buf)<n: break
    if (i-a)%step==0: fr.append((i,np.frombuffer(buf,np.uint8).reshape(H,W,3).copy()))
    i+=1
p.wait()
rows=(len(fr)+cols-1)//cols
sh=Image.new('RGB',(cols*W,rows*H),(8,8,8)); d=ImageDraw.Draw(sh)
for k,(f,im) in enumerate(fr):
    x=(k%cols)*W; y=(k//cols)*H; sh.paste(Image.fromarray(im),(x,y)); d.text((x+3,y+2),str(f),font=font,fill=(255,225,0))
sh.save(out,quality=86); print(out,sh.size,len(fr))
