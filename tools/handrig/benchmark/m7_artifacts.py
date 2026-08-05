"""M7 visual+motion artifacts. Uses ONLY the frozen render==solver frames and
solved trajectories. No renderer tuning, no cosmetic interpolation/easing:
GIF holds each solved knot for equal wall-time (phrase timing is uniform 0.5s)."""
import json, sys
import numpy as np
import imageio.v2 as imageio
from PIL import Image, ImageDraw
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
sys.path.insert(0,"/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark")
from adapters import SolverAdapter

BM="/Users/mattharvey/Sites/tone-forge/tools/handrig/benchmark"
R=f"{BM}/results/renders"; OUT=f"{BM}/results"
A=SolverAdapter()
naive=json.load(open(f"{OUT}/movement_report.json"))
traj=json.load(open(f"{OUT}/trajopt_report.json"))

# --- 2A: side-by-side GIF (naive | trajopt), frame-synced, one demo phrase ---
frames=[]
for i in range(4):
    ln=Image.open(f"{R}/best-one_shift_scale_naive_{i}.png").convert("RGB")
    lt=Image.open(f"{R}/best-one_shift_scale_trajopt_{i}.png").convert("RGB")
    h=min(ln.height,lt.height); ln=ln.resize((int(ln.width*h/ln.height),h)); lt=lt.resize((int(lt.width*h/lt.height),h))
    canvas=Image.new("RGB",(ln.width+lt.width+30,h+40),(255,255,255))
    canvas.paste(ln,(0,40)); canvas.paste(lt,(ln.width+30,40))
    d=ImageDraw.Draw(canvas)
    cm_n=naive["phrases"]["one_shift_scale"]["metrics"]["contact_fidelity"]["per_moment"][i]["contact_mm"]
    cm_t=traj["phrases"]["one_shift_scale"]["metrics"]["contact_fidelity"]["per_moment"][i]["contact_mm"]
    d.text((10,12),f"NAIVE  ev{i} contact={cm_n:.3f}mm",fill=(0,0,0))
    d.text((ln.width+40,12),f"TRAJOPT ev{i} contact={cm_t:.3f}mm",fill=(0,0,0))
    frames.append(np.array(canvas))
imageio.mimsave(f"{OUT}/m7_motion_naive_vs_trajopt.gif",frames,duration=1000,loop=0)  # 1s/frame, no easing
print("wrote m7_motion_naive_vs_trajopt.gif")

# --- 2B: root along-neck trajectory over events (ghost/overlay) ---
def along_neck(rep,ph):
    t=rep["trajectories"][ph]["knots"]
    return [float(A.root_of(np.array(k["mpfb_state"]))[0])*1000 for k in t]  # x = along-neck mm
fig,ax=plt.subplots(1,2,figsize=(12,4.5))
for j,ph in enumerate(["one_shift_scale","string_crossing"]):
    xn=along_neck(naive,ph); xt=along_neck(traj,ph)
    ev=list(range(len(xn)))
    ax[j].plot(ev,xn,"o-",label="naive",color="#c0392b",lw=2,ms=9)
    ax[j].plot(ev,xt,"s--",label="trajopt",color="#2980b9",lw=2,ms=9)
    ax[j].set_title(f"{ph}: hand-root along-neck (mm)"); ax[j].set_xlabel("event"); ax[j].set_ylabel("along-neck mm")
    ax[j].set_xticks(ev); ax[j].legend(); ax[j].grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}/m7_root_trajectory.png",dpi=110)
print("wrote m7_root_trajectory.png")
