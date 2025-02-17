#!/usr/bin/env python3
from flask import Flask, request, render_template_string, session, jsonify, send_from_directory, redirect, url_for
from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics
from PIL import Image, ImageDraw, ImageFont, ImageSequence
import cv2
import time
import threading
import datetime
import os
import sys
import re
import numpy as np
import ntplib  # 新增 ntplib 库用于 NTP 时间同步
from werkzeug.utils import secure_filename
import requests

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

# 默认的 RGB 顺序
DEFAULT_RGB_ORDER = "regular"

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
    options.rows = 32
    options.cols = 64
    options.chain_length = 1
    options.parallel = 1
    options.hardware_mapping = pixel_mapper  # 设置颜色顺序
    options.gpio_slowdown = 2
    options.brightness = 50
    return RGBMatrix(options=options)

# 应用 RGB 通道调节
def apply_rgb_factor(color, order="regular"):
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
def hex_to_tuple(hex_color, order="regular"):
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

# 时钟显示（带 RGB 调节）
class ClockDisplay(DisplayThread):
    def __init__(self, base_color, order, font_path, format_str, use_network_time):
        super().__init__()
        self.base_color = base_color
        self.order = order
        self.font_path = font_path
        self.format_str = format_str
        self.use_network_time = use_network_time
        self.font = ImageFont.truetype(self.font_path, size=10)

    def run(self):
        image = Image.new("RGB", (matrix.width, matrix.height))
        draw = ImageDraw.Draw(image)
        while not self.stop_flag:
            image.paste((0,0,0), (0,0,matrix.width,matrix.height))
            current_time = self.get_current_time()
            text_width = draw.textlength(current_time, font=self.font)
            adjusted_color = hex_to_tuple(self.base_color, self.order)  # 使用 hex_to_tuple 获取 (R, G, B) 元组
            draw.text(((matrix.width-text_width)//2, 10), 
                      current_time, font=self.font, fill=adjusted_color)  # 直接使用 (R, G, B) 元组
            matrix.SetImage(image)
            time.sleep(0.5)

    def get_current_time(self):
        if self.use_network_time:
            try:
                ntp_client = ntplib.NTPClient()
                response = ntp_client.request('pool.ntp.org')
                ntp_time = datetime.datetime.fromtimestamp(response.tx_time)
                return ntp_time.strftime(self.format_str)
            except Exception as e:
                print(f"Failed to fetch network time: {e}")
        return datetime.datetime.now().strftime(self.format_str)

# 文字显示（带 RGB 调节）
class ScrollText(DisplayThread):
    def __init__(self, text, base_color, speed, scroll, order, font_path):
        super().__init__()
        self.text = text
        self.base_color = base_color
        self.speed = speed
        self.scroll = scroll
        self.order = order
        self.font_path = font_path
        self.font = ImageFont.truetype(self.font_path, size=10)

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
        'dark_mode': session.get('dark_mode', False),
        'font_path': session.get('font_path', './fonts/DejaVuSans.ttf'),
        'clock_format': session.get('clock_format', '%H:%M:%S'),
        'use_network_time': session.get('use_network_time', False)
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
    fonts = [f for f in os.listdir('./fonts/') if f.endswith('.ttf')]
    dark_mode = session.get('dark_mode', False)
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>LED Matrix Control</title>
            <style>
                body.light-mode {
                    background-color: #fff;
                    color: #333;
                }
                body.dark-mode {
                    background-color: #333;
                    color: #fff;
                }
                .container { max-width: 800px; margin: 0 auto; padding: 20px; }
                .control-group { margin: 10px 0; padding: 15px; border: 1px solid #ccc; }
                .rgb-control { display: flex; align-items: center; margin: 5px 0; }
                .channel-label { width: 60px; }
                .red { color: #ff0000; }
                .green { color: #00ff00; }
                .blue { color: #0000ff; }
                .video-list { list-style-type: none; padding: 0; }
                .video-item { cursor: pointer; padding: 5px; border-bottom: 1px solid #eee; }
                .video-item:hover { background-color: #f0f0f0; }
                .image-list { list-style-type: none; padding: 0; }
                .image-item { cursor: pointer; padding: 5px; border-bottom: 1px solid #eee; }
                .image-item:hover { background-color: #f0f0f0; }
                button { margin-top: 10px; }
                .toggle-switch {
                    position: relative;
                    display: inline-block;
                    width: 60px;
                    height: 34px;
                }
                .toggle-switch input { opacity: 0; width: 0; height: 0; }
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
                    border-radius: 50%;
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
                        <select name="font" required>
                            {% for font in fonts %}
                                <option value="./fonts/{{ font }}" {{ 'selected' if font_path == './fonts/' + font }}>{% if font|length > 20 %}{{ font[:17] }}...{% else %}{{ font }}{% endif %}</option>
                            {% endfor %}
                        </select>
                        <button type="submit">显示</button>
                    </form>
                    <h3>时钟显示</h3>
                    <form id="clockForm" onsubmit="return submitForm(event)">
                        <input type="color" name="color" value="{{ color }}">
                        <select name="font" required>
                            {% for font in fonts %}
                                <option value="./fonts/{{ font }}" {{ 'selected' if font_path == './fonts/' + font }}>{% if font|length > 20 %}{{ font[:17] }}...{% else %}{{ font }}{% endif %}</option>
                            {% endfor %}
                        </select>
                        <input type="text" name="format" placeholder="格式（如 %H:%M:%S）" value="{{ clock_format }}">
                        <label><input type="checkbox" name="networkTime" {{ 'checked' if use_network_time }}> 使用网络时间</label>
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
                        document.querySelector('#clockForm select[name="font"]').value = data.font_path;
                        document.querySelector('#clockForm input[name="format"]').value = data.clock_format;
                        document.querySelector('#clockForm input[name="networkTime"]').checked = data.use_network_time;
                        document.querySelector('#textForm select[name="font"]').value = data.font_path;  // 添加这一行
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

                    fetch(`/rgb/<float:r>/<float:g>/<float:b>`, {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ red, green, blue })
                    });
                }

                // 设置亮度
                function setBrightness() {
                    const brightness = parseInt(document.getElementById('brightness').value);

                    fetch(`/brightness/<int:brightness>`, {
                        method: 'GET',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({ brightness })
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
                            scroll: formData.get('scroll') !== null,
                            fontPath: formData.get('font')  // 添加这一行
                        };
                    } else if (formId === 'clockForm') {
                        url = '/clock';
                        data = {
                            color: formData.get('color'),
                            fontPath: formData.get('font'),
                            formatStr: formData.get('format'),
                            useNetworkTime: formData.get('networkTime') !== null
                        };
                    }

                    fetch(url, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify(data)
                    })
                    .then(response => response.text())
                    .then(() => location.reload());
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
                        .then(response => response.text())
                        .then(() => location.reload());
                }

                // 暗黑模式切换逻辑
                document.getElementById('darkModeToggle').addEventListener('change', function() {
                    const isDarkMode = this.checked;
                    document.body.className = isDarkMode ? 'dark-mode' : 'light-mode';
                    fetch(`/dark_mode/${isDarkMode}`, {
                        method: 'POST'
                    }).then(response => response.text())
                      .then(() => console.log('Dark mode setting updated'));
                });
            </script>
        </body>
        </html>
    ''', rgb=session.get('rgb', [1.0, 1.0, 1.0]), brightness=session.get('brightness', 50),
       text=session.get('text', ''), color=session.get('color', '#ff0000'), speed=session.get('speed', 5),
       scroll=session.get('scroll', True), uploaded_image=session.get('uploaded_image', ''),
       videos=videos, images=images, rgb_order=session.get('rgb_order', DEFAULT_RGB_ORDER),
       dark_mode=session.get('dark_mode', False), fonts=fonts,
       font_path=session.get('font_path', './fonts/DejaVuSans.ttf'),
       clock_format=session.get('clock_format', '%H:%M:%S'),
       use_network_time=session.get('use_network_time', False))

@app.route('/rgb/<float:r>/<float:g>/<float:b>')
def set_rgb(r, g, b):
    global rgb_factors
    with color_lock:
        rgb_factors = [max(0, min(1, r)),
                       max(0, min(1, g)),
                       max(0, min(1, b))]
    session['rgb'] = rgb_factors
    return "RGB factors updated"

@app.route('/rgb_order/<string:order>')
def set_rgb_order(order):
    session['rgb_order'] = order
    return f"RGB order set to: {order}"

@app.route('/text', methods=['POST'])
def show_text():
    global current_thread
    stop_current()
    
    data = request.json
    text = data['text']
    color = data['color']  # 直接使用十六进制字符串
    speed = float(data['speed'])
    scroll = data['scroll']
    font_path = data['fontPath']  # 添加这一行
    
    current_thread = ScrollText(text, color, speed, scroll, session.get('rgb_order', DEFAULT_RGB_ORDER), font_path)
    current_thread.start()
    session['text'] = text
    session['color'] = color
    session['speed'] = speed
    session['scroll'] = scroll
    session['font_path'] = font_path  # 添加这一行
    return "Showing text"

@app.route('/clock', methods=['POST'])
def show_clock():
    global current_thread
    stop_current()
    
    data = request.json
    color = data['color']  # 直接使用十六进制字符串
    font_path = data['fontPath']
    format_str = data['formatStr']
    use_network_time = data['useNetworkTime']
    
    current_thread = ClockDisplay(color, session.get('rgb_order', DEFAULT_RGB_ORDER), font_path, format_str, use_network_time)
    current_thread.start()
    session['color'] = color
    session['font_path'] = font_path
    session['clock_format'] = format_str
    session['use_network_time'] = use_network_time
    return "Showing clock"

@app.route('/brightness/<int:brightness>')
def set_brightness(brightness):
    matrix.brightness = brightness
    session['brightness'] = brightness
    return f"Brightness set to {brightness}"

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

@app.route('/dark_mode/<boolean:mode>', methods=['POST'])
def toggle_dark_mode(mode):
    session['dark_mode'] = mode
    return "Dark mode setting updated"

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def secure_filename(filename):
    filename = re.sub(r'[^\w\s.-]', '', filename)
    return filename.replace(' ', '_')

if __name__ == '__main__':
    check_root_permission()
    matrix = setup_matrix(DEFAULT_RGB_ORDER)  # 设置硬件映射为 DEFAULT_RGB_ORDER
    try:
        app.run(host='::', port=8080, debug=False)
    finally:
        matrix.Clear()
        stop_current()
