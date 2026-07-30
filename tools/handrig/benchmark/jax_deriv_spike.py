"""Phase 1 feasibility spike (throwaway, does NOT touch frozen solver).
Reimplements a REPRESENTATIVE slice of the cost stack in jax.numpy — the op classes
that appear in the frozen evaluate: SE(3) root transform w/ euler (in-place->.at),
a 4-DoF finger FK chain, contact box-distance^2, a strain quadratic, and a clip-hinge
(the 'if x>0: +=x^2' disguised) — then checks jax reverse-mode grad vs high-quality
central differences. Answers: does this function CLASS autodiff correctly & is the
conversion mechanical?"""
import jax, jax.numpy as jnp
import numpy as np
jax.config.update("jax_enable_x64", True)   # match float64 solver

def euler_m(rx, ry, rz):
    cx,cy,cz = jnp.cos(jnp.array([rx,ry,rz])); sx,sy,sz = jnp.sin(jnp.array([rx,ry,rz]))
    Rx = jnp.array([[1,0,0],[0,cx,-sx],[0,sx,cx]])
    Ry = jnp.array([[cy,0,sy],[0,1,0],[-sy,0,cy]])
    Rz = jnp.array([[cz,-sz,0],[sz,cz,0],[0,0,1]])
    return Rz @ Ry @ Rx

def armature_world(root_loc, root_rot, fret_rot):
    R = euler_m(*root_rot)
    M = jnp.eye(4).at[:3,:3].set(fret_rot[:3,:3] @ R).at[:3,3].set(root_loc)  # was in-place
    return M

def finger_fk(mcp_flex, mcp_abd, pip, dip, AW, seg):
    # simple planar 4-joint chain in the palm frame, transformed by AW (representative)
    ang = jnp.array([mcp_flex, pip, dip])
    p = jnp.array([0.,0.,0.,1.]); pts=[]
    a = mcp_abd; c=jnp.cos(a); s=jnp.sin(a)
    Rabd = jnp.array([[c,-s,0,0],[s,c,0,0],[0,0,1,0],[0,0,0,1]])
    cum = AW @ Rabd; tot=0.
    for k in range(3):
        tot = tot + ang[k]
        ct,stt = jnp.cos(tot), jnp.sin(tot)
        step = jnp.array([[1,0,0,seg[k]*ct],[0,1,0,seg[k]*stt],[0,0,1,0],[0,0,0,1]])
        cum = cum @ step
        pts.append(cum @ p)
    return pts[-1][:3]   # fingertip mm-ish

def box_dist2(tip, lo, hi):                       # contact residual (piecewise, clip)
    over = jnp.clip(lo - tip, 0.) + jnp.clip(tip - hi, 0.)
    return jnp.sum(over*over)

def cost(x):
    root_loc=x[0:3]; root_rot=x[3:6]; fing=x[6:10]
    fret_rot=jnp.eye(4)
    AW = armature_world(root_loc, root_rot, fret_rot)
    seg=jnp.array([40.,25.,20.])
    tip = finger_fk(fing[0],fing[1],fing[2],fing[3], AW, seg)
    contact = box_dist2(tip, jnp.array([5.,-2.,-3.]), jnp.array([9.,2.,3.]))
    neutral=jnp.array([0.49,0.,0.79,0.52])
    strain = jnp.sum(jnp.array([1.,2.,0.9,0.6])*(fing-neutral)**2)  # (theta-c)^2
    ov = 9.0 - jnp.linalg.norm(tip[:2])            # a 'if ov>0: +=ov^2' hinge, as clip
    hinge = jnp.clip(ov,0.)**2
    return 8.0*contact + 2.4*strain + 30.0*hinge

x0 = np.array([0.001,-0.002,0.0, 0.05,-0.03,0.02, 0.5,0.0,0.8,0.5])
g_jax = np.asarray(jax.grad(cost)(jnp.array(x0)))
# high-quality central difference reference
def cd(f,x,h=1e-6):
    return np.array([(f(x+h*e)-f(x-h*e))/(2*h) for e in np.eye(len(x))])
g_cd = cd(lambda z: float(cost(jnp.array(z))), x0)
rel = np.linalg.norm(g_jax-g_cd)/(np.linalg.norm(g_cd)+1e-15)
print(f"cost(x0)         = {float(cost(jnp.array(x0))):.8f}")
print(f"||g_jax||        = {np.linalg.norm(g_jax):.6e}")
print(f"||g_jax - g_cd|| / ||g_cd|| = {rel:.3e}   (target < 1e-6)")
print(f"max abs elem err = {np.max(np.abs(g_jax-g_cd)):.3e}")
# jacobian of the residual VECTOR (Gauss-Newton needs J, not just grad of scalar)
def residuals(x):
    root_loc=x[0:3]; root_rot=x[3:6]; fing=x[6:10]
    AW=armature_world(root_loc,root_rot,jnp.eye(4)); seg=jnp.array([40.,25.,20.])
    tip=finger_fk(fing[0],fing[1],fing[2],fing[3],AW,seg)
    over=jnp.clip(jnp.array([5.,-2.,-3.])-tip,0.)+jnp.clip(tip-jnp.array([9.,2.,3.]),0.)
    neutral=jnp.array([0.49,0.,0.79,0.52]); strain_r=jnp.sqrt(jnp.array([1.,2.,0.9,0.6]))*(fing-neutral)
    ov=9.0-jnp.linalg.norm(tip[:2]); hinge_r=jnp.clip(ov,0.)
    return jnp.concatenate([jnp.sqrt(8.0)*over, jnp.sqrt(2.4)*strain_r, jnp.array([jnp.sqrt(30.0)*hinge_r])])
J = np.asarray(jax.jacobian(residuals)(jnp.array(x0)))
print(f"residual Jacobian shape = {J.shape}  (rows=residuals, cols=state) -> Gauss-Newton ready")
import time
f=jax.jit(cost); f(jnp.array(x0)).block_until_ready()
gj=jax.jit(jax.grad(cost)); gj(jnp.array(x0)).block_until_ready()
t=time.time(); [gj(jnp.array(x0)).block_until_ready() for _ in range(100)]; dt=(time.time()-t)/100
print(f"jit grad time ~ {dt*1e6:.0f} us/call  (FD grad would be ~{len(x0)}x a cost eval)")
