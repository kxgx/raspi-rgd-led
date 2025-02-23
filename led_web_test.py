#!/usr/bin/env python3
from flask import Flask, request, render_template_string, session, jsonify, send_from_directory, redirect, url_for
import cv2, time, threading, datetime, os, sys, re, subprocess, signal
import numpy as np
from werkzeug.utils import secure_filename
from werkzeug.routing import BaseConverter

class BooleanConverter(BaseConverter):
    """自定义布尔类型转换器"""
    def to_python(self, value):
        # 将 URL 中的字符串转换为 Python 的布尔值
        return value.lower() in ['true', 'yes', 't', '1']

    def to_url(self, value):
        # 将 Python 的布尔值转换为 URL 中的字符串
        return str(value).lower()

app = Flask(__name__)
app.url_map.converters['boolean'] = BooleanConverter
app.secret_key = 'your_secret_key'

# 定义全局变量
session_initialized = False

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'mp4', 'avi'}
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'avi'}  # 新增视频文件扩展名
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

current_process = None
stop_event = threading.Event()
DEFAULT_RGB_ORDER = "adafruit-hat" 
# 硬件配置默认值
HARDWARE_CONFIG = {
    'rows': 32,
    'cols': 64,
    'chain': 1,
    'parallel': 1,
    'gpio_mapping': 'adafruit-hat',
    'brightness': 50,
    'pwm_bits': 11,
    'rgb_sequence': 'RBG'
}

def build_base_args():
    return [
        f"--led-rows={HARDWARE_CONFIG['rows']}",
        f"--led-cols={HARDWARE_CONFIG['cols']}",
        f"--led-chain={HARDWARE_CONFIG['chain']}",
        f"--led-parallel={HARDWARE_CONFIG['parallel']}",
        f"--led-gpio-mapping={HARDWARE_CONFIG['gpio_mapping']}",
        f"--led-brightness={HARDWARE_CONFIG['brightness']}",
        f"--led-pwm-bits={HARDWARE_CONFIG['pwm_bits']}",
        f"--led-rgb-sequence={HARDWARE_CONFIG['rgb_sequence']}"
    ]

def run_command(cmd_args):
    global current_process
    stop_current()
    # 添加错误处理
    try:
        current_process = subprocess.Popen(
            cmd_args,
            preexec_fn=os.setsid  # 创建进程组以便终止整个进程树
        )
    except Exception as e:
        print(f"Command failed: {e}")

def stop_current():
    global current_process
    if current_process:
        try:
            os.killpg(os.getpgid(current_process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        current_process = None

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
input[type="number"] {
    width: 80px;
    margin: 5px;
    padding: 4px;
    border: 1px solid #ccc;
    border-radius: 4px;
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
<!-- 文本字体选择 -->
<h3>文本字体选择</h3>
<select id="textFontSelect" onchange="setTextFont(this.value)">
    {% for font in fonts %}
        <option value="{{ font }}" {% if session.text_font == font %}selected{% endif %}>{{ font }}</option>
    {% endfor %}
</select>

<!-- 时钟字体选择 -->
<h3>时钟字体选择</h3>
<select id="clockFontSelect" onchange="setClockFont(this.value)">
    {% for font in fonts %}
        <option value="{{ font }}" {% if session.clock_font == font %}selected{% endif %}>{{ font }}</option>
    {% endfor %}
</select>
                    <h3>文本显示</h3>
                    <form id="textForm" onsubmit="return submitForm(event)">
                        <input type="text" name="text" placeholder="输入文字" required value="{{ text }}">
                        <input type="color" name="color" value="{{ color }}">
                        <input type="number" name="x" placeholder="X坐标" value="0">
                        <input type="number" name="y" placeholder="Y坐标" value="0">
                        <input type="range" name="speed" min="1" max="10" step="1" value="{{ speed }}">
                        <label><input type="checkbox" name="scroll" {{ 'checked' if scroll else '' }}> 滚动</label>
                        <button type="submit">显示</button>
                    </form>
                    <h3>时钟显示</h3>
                    <form id="clockForm" onsubmit="return submitForm(event)">
                        <input type="color" name="color" value="{{ color }}">
                        <input type="number" name="x" placeholder="X坐标" value="0">
                        <input type="number" name="y" placeholder="Y坐标" value="0">
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
// 加载字体列表
fetch('/fonts')
    .then(response => response.json())
    .then(fonts => {
        const textSelect = document.getElementById('textFontSelect');
        const clockSelect = document.getElementById('clockFontSelect');
        
        fonts.forEach(font => {
            textSelect.appendChild(new Option(font, font));
            clockSelect.appendChild(new Option(font, font));
        });
        
        // 设置当前选择的字体
        textSelect.value = '{{ session.text_font|default("原神cn.bdf") }}';
        clockSelect.value = '{{ session.clock_font|default("6x13.bdf") }}';
    });
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
function setTextFont(font) {
    fetch(`/set_text_font/${encodeURIComponent(font)}`)
        .then(response => response.json())
        .then(data => {
            if(data.success) {
                console.log('文本字体已更新:', data.current_font);
            }
        });
}

function setClockFont(font) {
    fetch(`/set_clock_font/${encodeURIComponent(font)}`)
        .then(response => response.json())
        .then(data => {
            if(data.success) {
                console.log('时钟字体已更新:', data.current_font);
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
            x: parseInt(formData.get('x')) || 0,
            y: parseInt(formData.get('y')) || 0,
            speed: parseFloat(formData.get('speed')),
            scroll: formData.get('scroll') !== null
        };
    } else if (formId === 'clockForm') {
        url = '/clock';
        data = {
            color: formData.get('color'),
            x: parseInt(formData.get('x')) || 0,
            y: parseInt(formData.get('y')) || 0
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
    ''', rgb=session.get('rbg', [1.0, 1.0, 1.0]), brightness=session.get('brightness', 50),
       text=session.get('text', ''), color=session.get('color', '#ffffff'), speed=session.get('speed', 5),
       scroll=session.get('scroll', True), uploaded_image=session.get('uploaded_image', ''),
       videos=videos, images=images, rgb_order=session.get('rgb_order', DEFAULT_RGB_ORDER),
       dark_mode=session.get('dark_mode', False), hardware_mapping=hardware_mapping)

@app.route('/fonts')
def list_fonts():
    fonts = []
    try:
        # 使用动态路径获取方式
        font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "./rpi-rgb-led-matrix/fonts")
        
        # 添加目录存在性检查
        if not os.path.exists(font_dir):
            os.makedirs(font_dir, exist_ok=True)
            os.chmod(font_dir, 0o755)  # 设置适当权限
        for filename in os.listdir(font_dir):
            if filename.lower().endswith(('.ttf', '.otf', '.woff2', '.ttc', '.bdf')):
                fonts.append(filename)
    except Exception as e:
        print(f"Error accessing fonts directory: {e}")
        return jsonify([])  # 返回空列表而不是500错误
    
    return jsonify(fonts)

@app.route('/set_text_font/<font>')
def set_text_font(font):
    session['text_font'] = font
    return jsonify(success=True, current_font=font)

@app.route('/set_clock_font/<font>')
def set_clock_font(font):
    session['clock_font'] = font
    return jsonify(success=True, current_font=font)

@app.route('/rgb_order/<string:order>')
def set_rgb_order(order):
    session['rgb_order'] = order
    return jsonify({'success': True, 'rgb_order': order})

@app.route('/text', methods=['POST'])
def show_text():
    data = request.json
    text = data['text']
    color = data['color'].lstrip('#')
    r, g, b = [int(color[i:i+2], 16) for i in (0, 2, 4)]
    speed = data['speed']
    x = data.get('x', 0)  # 新增x坐标
    y = data.get('y', 0)  # 新增y坐标
    scroll_direction = -abs(speed) if data.get('scroll', True) else abs(speed)
    font = session.get('text_font', '原神cn.bdf')

    cmd = [
        'text-scroller',
        '-s', str(scroll_direction),
        '-f', f'./rpi-rgb-led-matrix/fonts/{font}',
        '-x', str(x),  # 新增x参数
        '-y', str(y),  # 新增y参数
        '-C', f'{r},{g},{b}',
        '-l', '-1'
    ] + build_base_args() + [text]
    
    run_command(cmd)
    return jsonify(success=True)

@app.route('/clock', methods=['POST'])
def show_clock():
    data = request.json
    color = data.get('color', '#FFFF00').lstrip('#')
    r, g, b = [int(color[i:i+2], 16) for i in (0, 2, 4)]
    x = data.get('x', 0)  # 新增x坐标
    y = data.get('y', 0)  # 新增y坐标
    font = session.get('clock_font', '6x13.bdf')

    cmd = [
        'clock',
        '-f', f'./rpi-rgb-led-matrix/fonts/{font}',
        '-x', str(x),  # 新增x参数
        '-y', str(y),  # 新增y参数
        '-C', f'{r},{g},{b}',
        '-d', '%H:%M:%S',
        '-d', '%Y-%m-%d'
    ] + build_base_args()
    
    run_command(cmd)
    return jsonify(success=True)

@app.route('/brightness/<int:brightness>')
def set_brightness(brightness):
    session['brightness'] = brightness
    return jsonify({'success': True, 'brightness': brightness})

@app.route('/rgb/<float:r>/<float:g>/<float:b>')
def set_rgb(r, g, b):
    # 实际需要将颜色设置应用到命令行工具参数
    session['rgb'] = [r, g, b]
    return jsonify(success=True)

@app.route('/command/<cmd>')
def command(cmd):
    if cmd == 'clear':
        # 终止当前运行的进程来清屏
        stop_current()
        return jsonify(success=True, message="Screen cleared")
    elif cmd == 'off':
        # 设置亮度为0
        run_command(['text-scroller', '-C', '0,0,0', '-B', '0,0,0', ' '])  # 显示黑色背景
        return jsonify(success=True, message="Display turned off")
    elif cmd == 'on':
        # 恢复默认亮度
        run_command(['text-scroller', '-C', '255,255,255', '-B', '0,0,0', ' '])  # 显示白色背景
        return jsonify(success=True, message="Display turned on")
    else:
        return jsonify(success=False, message="Unknown command")

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

@app.route('/video/<filename>')
def play_video(filename):
    video_path = os.path.join(UPLOAD_FOLDER, filename)
    cmd = [
        'video-viewer',
        '--led-slowdown-gpio=2',
        '-f'  # 全屏模式
    ] + build_base_args() + [video_path]
    
    run_command(cmd)
    return jsonify(success=True)

@app.route('/hardware', methods=['POST'])
def update_hardware():
    config = request.json
    for key in HARDWARE_CONFIG:
        if key in config:
            HARDWARE_CONFIG[key] = config[key]
    return jsonify(success=True)
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
    HARDWARE_CONFIG['gpio_mapping'] = mapping
    session['hardware_mapping'] = mapping
    return jsonify(success=True)
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
    try:
        app.run(host='::', port=8080, debug=False)
    finally:
        stop_current()
