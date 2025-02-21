#!/usr/bin/env python3
from flask import Flask, request, render_template_string, session, jsonify, send_from_directory, redirect, url_for
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
from PIL import Image, ImageDraw, ImageFont, ImageSequence
import cv2, time, threading, datetime, os, sys, re
import numpy as np
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # 设置一个密钥用于session加密

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi'}  # 新增视频文件扩展名
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

matrix = None
current_thread = None
stop_flag = False
rgb_factors = [1.0, 1.0, 1.0]  # 初始化 RGB 因子

# 默认 RGB 硬件顺序
DEFAULT_RGB_ORDER = "adafruit-hat"
session_initialized = False
TEXT_FONTS = "./fonts/原神cn.ttf"
CLOCK_FONTS = "DejaVuSans.ttf"

# 全局锁
color_lock = threading.Lock()

# 自定义布尔类型转换器
class BooleanConverter(app.url_map.converters['default']):
    def to_python(self, value):
        return value.lower() in ['true', 'yes', 't', '1']

    def to_url(self, value):
        return str(value).lower()

app.url_map.converters['boolean'] = BooleanConverter

# 初始化 LED 矩阵
def setup_matrix(pixel_mapper=DEFAULT_RGB_ORDER):
    options = RGBMatrixOptions()
    options.rows = 32       # 面板的行数
    options.cols = 64       # 面板的列数
    options.chain_length = 1  # 单个面板，不需要串联
    options.parallel = 1    # 单个面板，不需要并联
    options.hardware_mapping = pixel_mapper
    options.gpio_slowdown = 1  # 根据硬件调整
    options.brightness = 50    # 初始亮度
    options.scan_mode = 1  # 常见值为0或1
    options.multiplexing = 0  # 常见值为0或1
    return RGBMatrix(options=options)

# 应用 RGB 通道调节
def apply_rgb_factor(color, order=DEFAULT_RGB_ORDER):
    r, g, b = color
    if order == "grb":
        return (g, r, b)
    elif order == "rbg":
        return (r, b, g)
    elif order == "brg":
        return (b, r, g)
    elif order == "bgr":
        return (b, g, r)
    else:  # regular
        return (r, g, b)

# 十六进制颜色转换为 (R, G, B) 元组（调整 RGB 顺序）
def hex_to_tuple(hex_color, order=DEFAULT_RGB_ORDER):
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    adjusted_color = apply_rgb_factor((r, g, b), order)  # 调整为 (r, g, b)
    return adjusted_color  # 返回 (R, G, B) 元组

# 显示线程基类
class DisplayThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.stop_flag = False

class ClockDisplay(DisplayThread):
    def __init__(self, base_color, order):
        super().__init__()
        self.base_color = base_color
        self.order = order
        # 假设字体大小对于日期和时间是不同的，因此可以为日期设置一个不同的字体大小
        self.time_font = ImageFont.truetype(CLOCK_FONTS, size=14)
        self.date_font = ImageFont.truetype(CLOCK_FONTS, size=10)  # 示例：使用较小的字体显示日期

    def run(self):
        image = Image.new("RGB", (matrix.width, matrix.height))
        draw = ImageDraw.Draw(image)
        while not self.stop_flag:
            image.paste((0,0,0), (0,0,matrix.width,matrix.height))
            current_time = datetime.datetime.now().strftime("%H:%M:%S")
            current_date = datetime.datetime.now().strftime("%Y-%m-%d")
            time_width = draw.textlength(current_time, font=self.time_font)
            date_width = draw.textlength(current_date, font=self.date_font)
            adjusted_color = hex_to_tuple(self.base_color, self.order)  # 使用 hex_to_tuple 获取 (R, G, B) 元组
            
            # 绘制时间
            draw.text(((matrix.width-time_width)//2, 2), 
                      current_time, font=self.time_font, fill=adjusted_color)
            
            # 绘制日期，假设日期显示在时间下方
            draw.text(((matrix.width-date_width)//2, 15), 
                      current_date, font=self.date_font, fill=adjusted_color)  # 直接使用 (R, G, B) 元组
            
            matrix.SetImage(image)
            time.sleep(0.5)

# 文字显示（带 RGB 调节）
class ScrollText(DisplayThread):
    def __init__(self, text, base_color, speed, scroll, order):
        super().__init__()
        self.text = text
        self.base_color = base_color
        self.speed = speed
        self.scroll = scroll
        self.order = order
        self.font = ImageFont.truetype(TEXT_FONTS, size=10)

    def run(self):
        image = Image.new("RGB", (matrix.width, matrix.height))
        draw = ImageDraw.Draw(image)
        text_width = draw.textlength(self.text, font=self.font)
        pos = matrix.width if self.scroll else (matrix.width - text_width)//2
        
        while not self.stop_flag:
            image.paste((0,0,0), (0,0,matrix.width,matrix.height))
            adjusted_color = hex_to_tuple(self.base_color, self.order)  # 使用 hex_to_tuple 获取 (R, G, B) 元组
            draw.text((pos, 10), self.text, font=self.font, fill=adjusted_color)  # 直接使用 (R, G, B) 元组
            matrix.SetImage(image)
            if self.scroll:
                pos -= 1
                if pos + text_width < 0:
                    pos = matrix.width
            time.sleep(0.05 / self.speed)

# 图像显示
class ImageDisplay(DisplayThread):
    def __init__(self, image_path, order):
        super().__init__()
        self.image_path = image_path
        self.order = order

    def adjust_frame(self, frame):
        """
        调整图像帧的 RGB 通道
        """
        # 将 PIL 图像转换为 NumPy 数组
        frame_np = np.array(frame)

        # 应用 RGB 调整因子
        frame_np = frame_np.astype('float32')  # 转换为浮点数以便调整
        frame_np[..., 0] *= rgb_factors[0]  # 红色通道
        frame_np[..., 1] *= rgb_factors[1]  # 绿色通道
        frame_np[..., 2] *= rgb_factors[2]  # 蓝色通道

        # 限制像素值在 0-255 范围内
        frame_np = np.clip(frame_np, 0, 255).astype('uint8')

        # 应用 RGB 通道顺序调整
        if self.order == "grb":
            frame_np = frame_np[..., [1, 0, 2]]  # GRB 顺序
        elif self.order == "rbg":
            frame_np = frame_np[..., [0, 2, 1]]  # RBG 顺序
        elif self.order == "brg":
            frame_np = frame_np[..., [2, 0, 1]]  # BRG 顺序
        elif self.order == "bgr":
            frame_np = frame_np[..., [2, 1, 0]]  # BGR 顺序
        # 默认是 RGB 顺序，无需调整

        # 将 NumPy 数组转换回 PIL 图像
        return Image.fromarray(frame_np)

    def run(self):
        try:
            image = Image.open(self.image_path).convert('RGB')
            if image.format == 'GIF':
                # 处理 GIF 动画
                frames = [self.adjust_frame(frame.copy()) 
                         for frame in ImageSequence.Iterator(image)]
            else:
                # 处理静态图像
                frames = [self.adjust_frame(image.copy())]

            while not self.stop_flag:
                for frame in frames:
                    resized_frame = frame.resize((matrix.width, matrix.height), Image.LANCZOS)
                    matrix.SetImage(resized_frame.convert('RGB'))
                    duration = frame.info.get('duration', 100) / 1000.0  # 使用帧的持续时间
                    time.sleep(duration)
        except Exception as e:
            print(f"Error displaying image: {e}")

# 视频显示
class VideoDisplay(DisplayThread):
    def __init__(self, video_source, order):
        super().__init__()
        self.video_source = video_source
        self.order = order

    def adjust_frame(self, frame):
        """
        调整视频帧的 RGB 通道
        """
        # 应用 RGB 调整因子
        frame = frame.astype('float32')  # 转换为浮点数以便调整
        frame[..., 0] *= rgb_factors[0]  # 红色通道
        frame[..., 1] *= rgb_factors[1]  # 绿色通道
        frame[..., 2] *= rgb_factors[2]  # 蓝色通道

        # 限制像素值在 0-255 范围内
        frame = np.clip(frame, 0, 255).astype('uint8')

        # 应用 RGB 通道顺序调整
        if self.order == "grb":
            frame = frame[..., [1, 0, 2]]  # GRB 顺序
        elif self.order == "rbg":
            frame = frame[..., [0, 2, 1]]  # RBG 顺序
        elif self.order == "brg":
            frame = frame[..., [2, 0, 1]]  # BRG 顺序
        elif self.order == "bgr":
            frame = frame[..., [2, 1, 0]]  # BGR 顺序
        # 默认是 RGB 顺序，无需调整

        return frame

    def run(self):
        cap = cv2.VideoCapture(self.video_source)
        if not cap.isOpened():
            print(f"Error opening video source: {self.video_source}")
            return

        while not self.stop_flag:
            ret, frame = cap.read()
            if not ret:
                break

            try:
                # 颜色转换和调整
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = self.adjust_frame(frame)
                img = Image.fromarray(frame)
                matrix.SetImage(img.resize((matrix.width, matrix.height)))
            except Exception as e:
                print(f"Error processing frame: {e}")

        cap.release()

# 停止当前显示线程
def stop_current():
    global current_thread
    if current_thread and current_thread.is_alive():
        current_thread.stop_flag = True
        current_thread.join()
        current_thread = None

# 检查是否以root权限运行
def check_root_permission():
    if os.geteuid() != 0:
        print("This script must be run as root to access GPIO pins.")
        print("Please run the script with `sudo`:")
        print(f"  sudo {sys.argv[0]}")
        sys.exit(1)

# 获取当前状态
@app.route('/status')
def get_status():
    status = {
        'rgb': session.get('rgb', [1.0, 1.0, 1.0]),
        'brightness': session.get('brightness', 50),
        'text': session.get('text', ''),
        'color': session.get('color', '#ff0000'),
        'speed': session.get('speed', 5),
        'scroll': session.get('scroll', True),
        'uploaded_image': session.get('uploaded_image', ''),
        'video_source': session.get('video_source', ''),
        'rgb_order': session.get('rgb_order', DEFAULT_RGB_ORDER),
        'dark_mode': session.get('dark_mode', False)
    }
    return jsonify(status)

# 列出上传的视频文件
@app.route('/videos')
def list_videos():
    videos = []
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename.lower().endswith(tuple(ALLOWED_VIDEO_EXTENSIONS)):
            videos.append(filename)
    return jsonify(videos)

# 列出上传的图片文件
@app.route('/images')
def list_images():
    images = []
    for filename in os.listdir(UPLOAD_FOLDER):
        if filename.lower().endswith(tuple(ALLOWED_IMAGE_EXTENSIONS)):
            images.append(filename)
    return jsonify(images)

# Web 界面
@app.route('/')
def index():
    videos = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith(tuple(ALLOWED_VIDEO_EXTENSIONS))]
    images = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith(tuple(ALLOWED_IMAGE_EXTENSIONS))]
    dark_mode = session.get('dark_mode', False)
    hardware_mapping = session.get('hardware_mapping', DEFAULT_RGB_ORDER)
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>LED Matrix Control</title>
<style>
/* 基础样式 */
body.light-mode {
    background-color: #fff;
    color: #333;
}
body.dark-mode {
    background-color: #333;
    color: #fff;
}

.container {
    max-width: 800px;
    margin: 0 auto;
    padding: 20px;
    display: flex;
    flex-wrap: wrap;
    gap: 20px;
}

.control-group {
    margin: 10px 0;
    padding: 15px;
    border: 1px solid #ccc;
    flex: 1 1 300px;
    min-width: 300px;
}

.rgb-control {
    display: flex;
    align-items: center;
    margin: 5px 0;
}

.channel-label { width: 60px; }
.red { color: #ff0000; }
.green { color: #00ff00; }
.blue { color: #0000ff; }

.video-list, .image-list {
    list-style-type: none;
    padding: 0;
}

.video-item, .image-item {
    cursor: pointer;
    padding: 5px;
    border-bottom: 1px solid #eee;
}

.video-item:hover, .image-item:hover {
    background-color: #f0f0f0;
}

button {
    margin-top: 10px;
}

/* 开关样式 */
.toggle-switch {
    position: relative;
    display: inline-block;
    width: 60px;
    height: 34px;
}
.toggle-switch input { 
    opacity: 0;
    width: 0;
    height: 0;
}
.slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: #ccc;
    transition: .4s;
    border-radius: 34px;
}
.slider:before {
    position: absolute;
    content: "";
    height: 26px;
    width: 26px;
    left: 4px;
    bottom: 4px;
    background-color: white;
    transition: .4s;
    border-radius: 50%;
}
input:checked + .slider {
    background-color: #2196F3;
}
input:checked + .slider:before {
    transform: translateX(26px);
}

/* 小尺寸开关 */
.small-toggle-switch {
    width: 50px;
    height: 26px;
}
.small-slider {
    height: 22px;
    width: 22px;
    border-radius: 22px;
}
.small-slider:before {
    height: 18px;
    width: 18px;
    bottom: 2px;
    left: 2px;
}

/* 响应式设计 */
@media (max-width: 600px) {
    .control-group {
        flex-direction: column;
    }
    .rgb-control {
        flex-wrap: wrap;
    }
    input[type="number"],
    input[type="text"] {
        width: 100%;
        box-sizing: border-box;
    }
    button {
        width: 100%;
        padding: 12px;
    }
    
    /* 移动端暗黑模式调整 */
    body.dark-mode .control-group {
        border-color: #666;
    }
}
</style>
        </head>
        <body class="{{ 'dark-mode' if dark_mode else 'light-mode' }}">
            <div class="container">
                <h1>LED Matrix Control</h1>
                <div class="control-group">
                    <h3>系统控制</h3>
                    <button onclick="fetch('/command/clear')">清空屏幕</button>
                    <button onclick="fetch('/command/off')">关闭显示</button>
                    <button onclick="fetch('/command/on')">开启显示</button>
                    <h3>RGB 硬件映射</h3>
                    <select id="hardwareMapping" onchange="setHardwareMapping()">
                        <option value="adafruit-hat" {% if hardware_mapping == 'adafruit-hat' %}selected{% endif %}>Adafruit HAT</option>
                        <option value="regular" {% if hardware_mapping == 'regular' %}selected{% endif %}>Regular</option>
                        <option value="adafruit-hat-pwm" {% if hardware_mapping == 'adafruit-hat-pwm' %}selected{% endif %}>Adafruit HAT PWM</option>
                    </select>
                    <h3>暗黑模式</h3>
                    <label class="switch small-toggle-switch">
                        <input type="checkbox" id="darkModeToggle" {{ 'checked' if dark_mode }}>
                        <span class="slider round small-slider"></span>
                    </label>
                </div>
                <div class="control-group">
                    <h3>RGB 顺序</h3>
                    <select id="rgbOrder" onchange="setRGBOrder()">
                        <option value="regular" {% if rgb_order == 'regular' %}selected{% endif %}>Regular (RGB)</option>
                        <option value="grb" {% if rgb_order == 'grb' %}selected{% endif %}>GRB</option>
                        <option value="rbg" {% if rgb_order == 'rbg' %}selected{% endif %}>RBG</option>
                        <option value="brg" {% if rgb_order == 'brg' %}selected{% endif %}>BRG</option>
                        <option value="bgr" {% if rgb_order == 'bgr' %}selected{% endif %}>BGR</option>
                    </select>
                    <h3>RGB 通道调节</h3>
                    <div class="rgb-control">
                        <span class="channel-label red">Red:</span>
                        <input type="range" id="red" min="0" max="100" step="1" value="{{ rgb[0] * 100 }}"
                               oninput="updateValue('red', this.value)">
                        <span id="redValue">{{ rgb[0] * 100 }}%</span>
                    </div>
                    <div class="rgb-control">
                        <span class="channel-label green">Green:</span>
                        <input type="range" id="green" min="0" max="100" step="1" value="{{ rgb[1] * 100 }}"
                               oninput="updateValue('green', this.value)">
                        <span id="greenValue">{{ rgb[1] * 100 }}%</span>
                    </div>
                    <div class="rgb-control">
                        <span class="channel-label blue">Blue:</span>
                        <input type="range" id="blue" min="0" max="100" step="1" value="{{ rgb[2] * 100 }}"
                               oninput="updateValue('blue', this.value)">
                        <span id="blueValue">{{ rgb[2] * 100 }}%</span>
                    </div>
                    <button onclick="applyRGB()">应用通道设置</button>
                </div>

                <div class="control-group">
                    <h3>文本显示</h3>
                    <form id="textForm" onsubmit="return submitForm(event)">
                        <input type="text" name="text" placeholder="输入文字" required value="{{ text }}">
                        <input type="color" name="color" value="{{ color }}">
                        <input type="range" name="speed" min="1" max="10" step="1" value="{{ speed }}">
                        <label><input type="checkbox" name="scroll" {{ 'checked' if scroll else '' }}> 滚动</label>
                        <button type="submit">显示</button>
                    </form>
                    <h3>时钟显示</h3>
                    <form id="clockForm" onsubmit="return submitForm(event)">
                        <input type="color" name="color" value="{{ color }}">
                        <button type="submit">显示时钟</button>
                    </form>
                    <h3>亮度调节</h3>
                    <input type="range" id="brightness" min="0" max="100" step="1" value="{{ brightness }}">
                    <button onclick="setBrightness()">设置亮度</button>
                </div>

                <div class="control-group">
                    <h3>上传图片</h3>
                    <form id="uploadImageForm" enctype="multipart/form-data" onsubmit="return uploadImage(event)">
                        <input type="file" name="file" accept=".png,.jpg,.jpeg,.gif" required>
                        <button type="submit">上传图片</button>
                    </form>
                    {% if uploaded_image %}
                        <p>已上传的图片: <a href="{{ url_for('uploaded_file', filename=uploaded_image) }}">{{ uploaded_image }}</a></p>
                    {% endif %}
                   <h3>显示本地图片</h3>
                    <ul class="image-list" id="imageList">
                        {% for image in images %}
                            <li class="image-item" onclick="showImage('{{ image }}')">{{ image }}</li>
                        {% endfor %}
                    </ul>
                </div>

                <div class="control-group">
                    <h3>上传视频</h3>
                    <form id="uploadVideoForm" enctype="multipart/form-data" onsubmit="return uploadVideo(event)">
                        <input type="file" name="file" accept=".mp4,.avi" required>
                        <button type="submit">上传视频</button>
                    </form>
                    <ul class="video-list" id="uploadedVideoList">
                        {% for video in videos %}
                            <li class="video-item" onclick="playVideo('{{ video }}')">{{ video }}</li>
                        {% endfor %}
                    </ul>
                    <h3>播放视频</h3>
                    <ul class="video-list" id="videoList">
                        {% for video in videos %}
                            <li class="video-item" onclick="playVideo('{{ video }}')">{{ video }}</li>
                        {% endfor %}
                    </ul>
                    <h3>通过 URL 播放视频</h3>
                    <form id="videoUrlForm" onsubmit="return playVideoFromUrl(event)">
                        <input type="text" name="url" placeholder="输入视频 URL" required>
                        <button type="submit">播放</button>
                    </form>
                </div>
            </div>

            <script>
                // 获取初始状态
// 获取初始状态
fetch('/status')
    .then(response => response.json())
    .then(data => {
        document.getElementById('red').value = data.rgb[0] * 100;
        document.getElementById('redValue').textContent = `${data.rgb[0] * 100}%`;
        document.getElementById('green').value = data.rgb[1] * 100;
        document.getElementById('greenValue').textContent = `${data.rgb[1] * 100}%`;
        document.getElementById('blue').value = data.rgb[2] * 100;
        document.getElementById('blueValue').textContent = `${data.rgb[2] * 100}%`;
        document.getElementById('brightness').value = data.brightness;
        document.getElementById('text').value = data.text;
        document.getElementById('color').value = data.color;
        document.getElementById('speed').value = data.speed;
        document.getElementById('scroll').checked = data.scroll;
        document.getElementById('rgbOrder').value = data.rgb_order;
        document.getElementById('darkModeToggle').checked = data.dark_mode;
    });

                // 更新滑块值显示
                function updateValue(channel, value) {
                    document.getElementById(`${channel}Value`).textContent = `${value}%`;
                }

                // 应用 RGB 通道设置
function applyRGB() {
    const red = parseFloat(document.getElementById('red').value) / 100;
    const green = parseFloat(document.getElementById('green').value) / 100;
    const blue = parseFloat(document.getElementById('blue').value) / 100;

    // 修正 URL，动态插入 RGB 值
    fetch(`/rgb/${red}/${green}/${blue}`, {
        method: 'GET'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById('red').value = data.rgb[0] * 100;
            document.getElementById('redValue').textContent = `${data.rgb[0] * 100}%`;
            document.getElementById('green').value = data.rgb[1] * 100;
            document.getElementById('greenValue').textContent = `${data.rgb[1] * 100}%`;
            document.getElementById('blue').value = data.rgb[2] * 100;
            document.getElementById('blueValue').textContent = `${data.rgb[2] * 100}%`;
        }
    });
}

                // 设置亮度
function setBrightness() {
    const brightness = parseInt(document.getElementById('brightness').value);
    // 修正 URL，使用模板字符串动态插入亮度值
    fetch(`/brightness/${brightness}`, {
        method: 'GET'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById('brightness').value = data.brightness;
        }
    });
}
                // 提交表单通用函数
function submitForm(event) {
    event.preventDefault(); // 阻止默认表单提交行为
    const formId = event.target.id;
    const formData = new FormData(document.getElementById(formId));

    let url;
    let data;

    if (formId === 'textForm') {
        url = '/text';
        data = {
            text: formData.get('text'),
            color: formData.get('color'),
            speed: parseFloat(formData.get('speed')),
            scroll: formData.get('scroll') !== null
        };
    } else if (formId === 'clockForm') {
        url = '/clock';
        data = {
            color: formData.get('color')
        };
    }

    fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // 手动更新页面内容
            document.getElementById('text').value = data.text;
            document.getElementById('color').value = data.color;
            document.getElementById('speed').value = data.speed;
            document.getElementById('scroll').checked = data.scroll;
        }
    });
}

                function uploadImage(event) {
                    event.preventDefault();
                    const formData = new FormData(document.getElementById('uploadImageForm'));
                    fetch('/upload_image', {
                        method: 'POST',
                        body: formData
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            location.reload();
                        }
                    });
                }

                function uploadVideo(event) {
                    event.preventDefault();
                    const formData = new FormData(document.getElementById('uploadVideoForm'));
                    fetch('/upload_video', {
                        method: 'POST',
                        body: formData
                    })
                    .then(response => response.json())
                    .then(data => {
                        if (data.success) {
                            location.reload();
                        }
                    });
                }

                function playVideo(videoSource) {
                    fetch(`/video/${encodeURIComponent(videoSource)}`)
                        .then(response => response.text())
                        .then(() => location.reload());
                }

                function showImage(imageSource) {
                    fetch(`/image/${encodeURIComponent(imageSource)}`)
                        .then(response => response.text())
                        .then(() => location.reload());
                }

                function playVideoFromUrl(event) {
                    event.preventDefault();
                    const formData = new FormData(document.getElementById('videoUrlForm'));
                    const videoUrl = formData.get('url');
                    fetch(`/videourl/${encodeURIComponent(videoUrl)}`)
                        .then(response => response.text())
                        .then(() => location.reload());
                }

function setRGBOrder() {
    const selectedOrder = document.getElementById('rgbOrder').value;
    fetch(`/rgb_order/${selectedOrder}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                document.getElementById('rgbOrder').value = data.rgb_order;
            }
        });
}
function setHardwareMapping() {
    const selectedMapping = document.getElementById('hardwareMapping').value;
    fetch(`/hardware_mapping/${selectedMapping}`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                document.getElementById('hardwareMapping').value = data.hardware_mapping;
            }
        });
}
document.getElementById('darkModeToggle').addEventListener('change', function() {
    const isDarkMode = this.checked;
    document.body.className = isDarkMode ? 'dark-mode' : 'light-mode';
    // 修正 URL，动态插入 isDarkMode 的值
    fetch(`/dark_mode/${isDarkMode}`, {
        method: 'POST'
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            document.getElementById('darkModeToggle').checked = data.dark_mode;
        }
    });
});
            </script>
        </body>
        </html>
    ''', rgb=session.get('rgb', [1.0, 1.0, 1.0]), brightness=session.get('brightness', 50),
       text=session.get('text', ''), color=session.get('color', '#ffffff'), speed=session.get('speed', 5),
       scroll=session.get('scroll', True), uploaded_image=session.get('uploaded_image', ''),
       videos=videos, images=images, rgb_order=session.get('rgb_order', DEFAULT_RGB_ORDER),
       dark_mode=session.get('dark_mode', False), hardware_mapping=hardware_mapping)

@app.route('/rgb/<float:r>/<float:g>/<float:b>')
def set_rgb(r, g, b):
    global rgb_factors
    with color_lock:
        rgb_factors = [max(0, min(1, r)),
                       max(0, min(1, g)),
                       max(0, min(1, b))]
    session['rgb'] = rgb_factors
    return jsonify({'success': True, 'rgb': rgb_factors})

@app.route('/rgb_order/<string:order>')
def set_rgb_order(order):
    session['rgb_order'] = order
    return jsonify({'success': True, 'rgb_order': order})

@app.route('/text', methods=['POST'])
def show_text():
    global current_thread
    stop_current()
    
    data = request.json
    text = data['text']
    color = data['color']  # 直接使用十六进制字符串
    speed = float(data['speed'])
    scroll = data['scroll']
    
    current_thread = ScrollText(text, color, speed, scroll, session.get('rgb_order', DEFAULT_RGB_ORDER))
    current_thread.start()
    session['text'] = text
    session['color'] = color
    session['speed'] = speed
    session['scroll'] = scroll
    return "Showing text"

@app.route('/clock', methods=['POST'])
def show_clock():
    global current_thread
    stop_current()
    
    data = request.json
    color = data['color']  # 获取时钟颜色
    session['clock_color'] = color  # 存储时钟颜色到 session
    
    current_thread = ClockDisplay(color, session.get('rgb_order', DEFAULT_RGB_ORDER))
    current_thread.start()
    return "Showing clock"

@app.route('/brightness/<int:brightness>')
def set_brightness(brightness):
    matrix.brightness = brightness
    session['brightness'] = brightness
    return jsonify({'success': True, 'brightness': brightness})

@app.route('/dark_mode/<boolean:mode>', methods=['POST'])
def toggle_dark_mode(mode):
    session['dark_mode'] = mode
    return jsonify({'success': True, 'dark_mode': mode})

@app.route('/rgb/<float:r>/<float:g>/<float:b>')
def set_rgb_values(r, g, b):  # 重命名为唯一的名称
    global rgb_factors
    with color_lock:
        rgb_factors = [max(0, min(1, r)),
                       max(0, min(1, g)),
                       max(0, min(1, b))]
    session['rgb'] = rgb_factors
    return jsonify({'success': True, 'rgb': rgb_factors})

@app.route('/command/<cmd>')
def command(cmd):
    global current_thread
    stop_current()
    
    if cmd == 'clear':
        matrix.Clear()
        return "Screen cleared"
    elif cmd == 'off':
        matrix.brightness = 0
        session['brightness'] = 0
        return "Display off"
    elif cmd == 'on':
        matrix.brightness = 50
        session['brightness'] = 50
        return "Display on"
    return "Unknown command"

@app.route('/upload_image', methods=['POST'])
def upload_image():
    file = request.files['file']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        session['uploaded_image'] = filename
        return jsonify({'success': True})
    else:
        return jsonify({'success': False})

@app.route('/upload_video', methods=['POST'])
def upload_video():
    file = request.files['file']
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        session['video_source'] = filename
        start_display_thread(filename)
        return jsonify({'success': True})
    else:
        return jsonify({'success': False})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/video/<path:video_source>')
def play_video(video_source):
    global current_thread
    stop_current()
    
    session['video_source'] = video_source
    video_path = os.path.join(UPLOAD_FOLDER, video_source)
    current_thread = VideoDisplay(video_path, session.get('rgb_order', DEFAULT_RGB_ORDER))
    current_thread.start()
    return "Playing video"

@app.route('/image/<path:image_source>')
def show_image(image_source):
    global current_thread
    stop_current()
    
    session['uploaded_image'] = image_source
    image_path = os.path.join(UPLOAD_FOLDER, image_source)
    current_thread = ImageDisplay(image_path, session.get('rgb_order', DEFAULT_RGB_ORDER))
    current_thread.start()
    return "Showing image"

@app.route('/videourl/<path:video_url>')
def play_video_from_url(video_url):
    global current_thread
    stop_current()
    
    session['video_source'] = video_url
    current_thread = VideoDisplay(video_url, session.get('rgb_order', DEFAULT_RGB_ORDER))
    current_thread.start()
    return "Playing video from URL"

@app.before_request
def initialize_session():
    global session_initialized
    if not session_initialized:
        session.setdefault('hardware_mapping', DEFAULT_RGB_ORDER)  # 设置默认值
        session_initialized = True

# 硬件映射路由
@app.route('/hardware_mapping/<string:mapping>')
def set_hardware_mapping(mapping):
    global matrix
    session['hardware_mapping'] = mapping
    stop_current()
    matrix = setup_matrix(mapping)  # 重新初始化矩阵
    return jsonify({'success': True, 'hardware_mapping': mapping})

@app.route('/dark_mode/<boolean:mode>', methods=['POST'])
def set_dark_mode(mode):  # 重命名为唯一的名称
    session['dark_mode'] = mode
    return jsonify({'success': True, 'dark_mode': mode})

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def secure_filename(filename):
    filename = re.sub(r'[^\w\s.-]', '', filename)
    return filename.replace(' ', '_')

if __name__ == '__main__':
    check_root_permission()
    matrix = setup_matrix(DEFAULT_RGB_ORDER)  # 使用默认硬件映射
    print(f"Matrix configured size: {matrix.width}x{matrix.height}")
    try:
        app.run(host='::', port=8080, debug=False)
    finally:
        matrix.Clear()
        stop_current()
