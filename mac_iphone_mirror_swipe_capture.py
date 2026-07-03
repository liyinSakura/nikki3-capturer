import pyautogui
import os
import time
from pynput import keyboard  # 需要先 pip install pynput

# ========================================================
# 0. 【双重安全制动配置】
# ========================================================
# 机制一：强行把鼠标甩到屏幕最左上角，脚本会立刻崩溃停止
pyautogui.FAIL_SAFE = True 

# 机制二：按下键盘 Esc 键，脚本会安全标记并优雅退出
IS_RUNNING = True

def on_press(key):
    global IS_RUNNING
    try:
        if key == keyboard.Key.esc:
            print("\n🚨 检测到按下 [Esc] 键！正在紧急制动，将在当前动作完成后停止...")
            IS_RUNNING = False
            return False # 停止键盘监听
    except Exception as e:
        print(f"监听键盘出错: {e}")

# 启动异步键盘监听器
listener = keyboard.Listener(on_press=on_press)
listener.start()

# ========================================================
# 1. 【基本配置参数】
# ========================================================
CATEGORY = "萤光之灵"  
OUTPUT_ROOT = "wardrobe-captures"
ITEMS_PER_FOLDER = 10  
BATCH_SIZE = 100  
CONSECUTIVE_SIMILAR_LIMIT = 2  # 连续多少张高度相似时，判定到底部并自动停止
HASH_HAMMING_THRESHOLD = 4     # 感知哈希距离阈值，越大越容易判定为“相似”

X = 80  
Y = 100
WIDTH = 290
HEIGHT = 620

print(f"🚀 脚本启动，目标分类：【{CATEGORY}】...")
print(f"📁 截图将保存到：{os.path.join(OUTPUT_ROOT, CATEGORY)}")
print("💡 【安全提示】:")
print("   - 随时按下键盘 [Esc] 键：可在完成当前单张截图/滑动后优雅停止。")
print("   - 强行将鼠标甩到屏幕 [最左上角]：可无条件瞬间中止脚本。")
print("--------------------------------------------------")

# ========================================================
# 2. 启动时即允许自定义起始编号
# ========================================================
init_id_input = input("⌨️ 请输入本次启动的【起始图片编号】(直接回车默认从 1 开始): ").strip()
if init_id_input:
    try:
        current_id = int(init_id_input)
        print(f"🎯 本次将从第 {current_id} 张图开始。")
    except ValueError:
        print("⚠️ 输入错误，使用默认编号 1。")
        current_id = 1
else:
    current_id = 1

# 自动计算滑动坐标
center_x = X + WIDTH // 2
drag_start_y = Y + int(HEIGHT * 0.85)   
drag_dist_y = int(HEIGHT * 0.60)        
drag_end_y = drag_start_y - drag_dist_y 

if drag_end_y < Y:
    drag_end_y = Y + 10 

def image_hash(image):
    # 裁掉边缘和状态栏区域，降低时间/动态条带来的误差
    left = int(image.width * 0.08)
    top = int(image.height * 0.10)
    right = int(image.width * 0.92)
    bottom = int(image.height * 0.92)
    core = image.crop((left, top, right, bottom))

    gray = core.convert("L").resize((8, 8))
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)

    result = 0
    for p in pixels:
        result = (result << 1) | int(p >= avg)
    return result

def hash_distance(a, b):
    xor_value = a ^ b
    if hasattr(xor_value, "bit_count"):
        return xor_value.bit_count()
    return bin(xor_value).count("1")

try:
    print("\n⏳ 准备就绪，正在强行唤醒并置顶 'iPhone 镜像'...")
    os.system('''osascript -e 'tell application "iPhone Mirroring" to activate' 2>/dev/null''')
    time.sleep(1.5)

    previous_hash = None
    consecutive_similar_count = 0
    reached_bottom = False

    while IS_RUNNING:
        batch_end_id = current_id + BATCH_SIZE - 1
        print(f"\n🎬 开始新一批次截图：从第 {current_id} 张 到 第 {batch_end_id} 张")
        
        for i in range(BATCH_SIZE):
            # 每次循环开始前，先检查用户有没有按过 Esc
            if not IS_RUNNING:
                break

            group_index = (current_id - 1) // ITEMS_PER_FOLDER + 1
            save_dir = os.path.join(OUTPUT_ROOT, CATEGORY, f"第{group_index}组")
            
            if not os.path.exists(save_dir):
                os.makedirs(save_dir)
                print(f"\n📂 创建新文件夹: {save_dir}")

            print(f"📸 [编号 {current_id} / 目标终点 {batch_end_id}] 正在截图...")
            screenshot = pyautogui.screenshot(region=(X, Y, WIDTH, HEIGHT))
            current_hash = image_hash(screenshot)
            distance = None
            if previous_hash is None:
                consecutive_similar_count = 1
            else:
                distance = hash_distance(current_hash, previous_hash)
                if distance <= HASH_HAMMING_THRESHOLD:
                    consecutive_similar_count += 1
                else:
                    consecutive_similar_count = 1
            previous_hash = current_hash
            
            file_name = f"{CATEGORY}_{current_id}.png"
            file_path = os.path.join(save_dir, file_name)
            screenshot.save(file_path)
            print(f"✅ 已保存: {file_path}")
            
            current_id += 1

            if consecutive_similar_count >= CONSECUTIVE_SIMILAR_LIMIT:
                reached_bottom = True
                if distance is not None:
                    print(
                        f"\n🧭 检测到最近连续 {consecutive_similar_count} 张截图高度相似"
                        f"（哈希距离={distance}），已判定滑动到底部并自动停止。"
                    )
                else:
                    print(
                        f"\n🧭 检测到最近连续 {consecutive_similar_count} 张截图高度相似，"
                        "已判定滑动到底部并自动停止。"
                    )
                break
            
            if i == BATCH_SIZE - 1:
                break
                
            # 滑动前再次确认安全开关
            if not IS_RUNNING:
                break

            print("💧 正在执行向上滑动...")
            pyautogui.moveTo(center_x, drag_start_y)
            pyautogui.dragTo(center_x, drag_end_y, duration=1.5, button='left')
            
            print("⏳ 等待 1.5 秒让滚动动画静止...")
            time.sleep(1.5)

        # 检查是否是因为按了 Esc 导致的内循环中断
        if not IS_RUNNING:
            print(f"\n🛑 触发键盘紧急制动！程序已安全暂停。下一张推荐编号为: {current_id}")
            break

        if reached_bottom:
            print(f"✅ 已到底部，任务自动结束。下一张推荐编号为: {current_id}")
            break

        # ========================================================
        # 3. 人工交互环节
        # ========================================================
        print(f"\n📊 这一批次共 {BATCH_SIZE} 张截图已完成！")
        user_choice = input("❓ 是否需要继续截下一批？(y/n): ").strip().lower()
        
        if user_choice != 'y':
            print("\n🛑 收到指令，结束程序。")
            break
            
        print(f"💡 提示：按规律下一张编号推荐为 {current_id}")
        next_id_input = input(f"⌨️ 请输入接下来的起始编号 (直接回车默认使用 {current_id}): ").strip()
        
        if next_id_input:
            try:
                current_id = int(next_id_input)
                print(f"🎯 编号已重置为 {current_id}。")
            except ValueError:
                print(f"⚠️ 输入无效，继续使用默认编号 {current_id}。")
            
        print("\n🔄 请将手机画面切换/对齐到你想继续截屏的位置...")
        print("⏳ 5 秒后将自动开始下一批次...")
        time.sleep(5.0)
        previous_hash = None
        consecutive_similar_count = 0

    print("\n🎉 任务结束。")

except pyautogui.FailSafeException:
    print("\n💥 [触发物理防线]：检测到鼠标被强行移至屏幕边缘，脚本已立即崩溃强制停止！")
except Exception as e:
    print(f"❌ 运行发生错误: {e}")
finally:
    # 确保退出时关闭监听器，防止终端残留进程
    if listener.running:
        listener.stop()