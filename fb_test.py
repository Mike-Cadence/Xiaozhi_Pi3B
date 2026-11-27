import mmap, time, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SYS = "/sys/class/graphics/fb0"

def sysfs(name):
    try:
        with open(f"{SYS}/{name}") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

W, H = map(int, sysfs("virtual_size").split(","))
BPP = int(sysfs("bits_per_pixel"))
STRIDE = int(sysfs("stride")) if sysfs("stride") else W * BPP // 8
print(f"[fb0] {W}x{H}  {BPP}bpp  stride={STRIDE}")

fb_file = open("/dev/fb0", "r+b", buffering=0)
fb = mmap.mmap(fb_file.fileno(), STRIDE * H,
               mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)

def rgb_to_rgb565(img_rgb):
    """numpy 向量化：RGB (H,W,3) -> RGB565 (H,W) 小端"""
    arr = np.array(img_rgb).astype(np.uint16)
    r = (arr[..., 0] >> 3) << 11
    g = (arr[..., 1] >> 2) << 5
    b = arr[..., 2] >> 3
    rgb565 = r | g | b
    return rgb565.tobytes("C")

def rgb_to_xrgb8888(img_rgb):
    """RGB (H,W,3) -> BGRA (H,W,4)"""
    arr = np.array(img_rgb)
    bgra = np.concatenate([arr[:,:,::-1],
                           np.full((*arr.shape[:2], 1), 255, dtype=np.uint8)], axis=2)
    return bgra.tobytes("C")

def flush(im):
    if BPP == 16:
        raw = rgb_to_rgb565(im)
    elif BPP == 32:
        raw = rgb_to_xrgb8888(im)
    else:
        print(f"[!] 未知色深 {BPP}bpp，无法 flush")
        sys.exit(1)
    # stride 对齐：显存行宽可能大于图像行宽
    row = im.width * BPP // 8
    if STRIDE == row:
        fb[:len(raw)] = raw
    else:
        for y in range(im.height):
            off = y * STRIDE
            fb[off:off + row] = raw[y * row:(y + 1) * row]

# ---- 静态测试图 ----
img = Image.new("RGB", (W, H), "black")
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
except OSError:
    font = ImageFont.load_default()

colors = ["red", "orange", "yellow", "green", "cyan", "blue"]
bw = W // len(colors)
for i, c in enumerate(colors):
    d.rectangle([i * bw, 0, (i + 1) * bw - 1, 80], fill=c)
for x in range(0, W, 50):
    d.line([x, 0, x, H], fill=(50, 50, 50))
for y in range(0, H, 50):
    d.line([0, y, W, y], fill=(50, 50, 50))
d.rectangle([0, 0, W - 1, H - 1], outline="white")
d.text((20, 120), f"fbdev OK  {W}x{H}  {BPP}bpp", font=font, fill="white")
flush(img)
print(f"[1/2] 静态图已输出，3 秒后进动画")
time.sleep(3)

# ---- 动画帧率测试 ----
N = 120
t0 = time.monotonic()
for i in range(N):
    d.rectangle([0, 200, W, H - 1], fill="black")
    x = int((W - 80) * (i % 60) / 59)
    d.rectangle([x, 300, x + 80, 380], fill="lime")
    flush(img)
dt = time.monotonic() - t0
print(f"[2/2] {N} 帧耗时 {dt:.2f}s，约 {N/dt:.1f} fps")