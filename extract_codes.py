import base64, json, os, re, requests, sys
from datetime import datetime
from typing import Optional

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
DEFAULT_CONFIG = {
    "BASE_DIR": "wardrobe-captures",
    "OUTPUT_BASE_DIR": "wardrobe-outputs",
    "API_BASE": "http://127.0.0.1:8080/v1",
    "MODEL": "你的新多模态模型名称.gguf",
    "MIN_EXPECTED_COUNT": 5,
    "TIMEOUT": 60,
    "IMAGE_EXTS": [".png", ".jpg", ".jpeg", ".webp", ".bmp"],
    "HIGH_PRIORITY": ["袜子", "饰品"],
    "LOW_PRIORITY": ["下装", "鞋子", "妆容"],
    "LOG_FILE_NAME": "数据清洗及错误盘点.md"
}


def resolve_config_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), path))


def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            cfg.update(file_cfg)
        except Exception as e:
            print(f"[WARN] 无法加载 config.json，使用默认配置: {e}")
    return cfg


config = load_config()
BASE_DIR = resolve_config_path(config["BASE_DIR"])
OUTPUT_BASE_DIR = resolve_config_path(config["OUTPUT_BASE_DIR"])
API_BASE = config["API_BASE"]
MODEL = config["MODEL"]
MIN_EXPECTED_COUNT = config["MIN_EXPECTED_COUNT"]
TIMEOUT = config["TIMEOUT"]
IMAGE_EXTS = set(config["IMAGE_EXTS"])
HIGH_PRIORITY = config["HIGH_PRIORITY"]
LOW_PRIORITY = config["LOW_PRIORITY"]
MD_LOG_PATH = os.path.join(OUTPUT_BASE_DIR, config["LOG_FILE_NAME"])

# ========================================================
# 1. 【核心配置】
# ========================================================
PROMPT = (
    "你是一个高精度的游戏数据分析助手。请仔细观察这张游戏图鉴截图，提取出图中所有服装卡片左上角的数字编码。\n\n"
    "请严格遵守以下提取规则：\n"
    "1. 【扫描顺序】：按照从上到下、从左到右的网格顺序依次扫描每一张卡片，确保不漏掉任何一个。\n"
    "2. 【保持原样】：必须严格保持图片中看到的数字格式（允许三位、四位或五位数字）。如果遇到形如 \"001\"、\"015\"、\"10024\" 的数字，必须完整保留，绝对不能漏掉任何一位，也绝对不能擅自补零。\n"
    "3. 【去重输出】：提取完成后，请去除重复的数字，并按照数字从小到大的顺序进行排列。\n\n"
    "【输出格式】：\n"
    "请直接输出去重并排序后的数字列表，用逗号分隔，不要包含任何额外的解释或开场白。\n"
    "示例：140, 141, 10024, 10025..."
)

TIMEOUT = 60 
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MD_LOG_PATH = os.path.join(OUTPUT_BASE_DIR, "数据清洗及错误盘点.md")


def encode_image(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _filter_prefix_tokens(tokens: list) -> tuple[list, list]:
    # 如果同一组输出同时出现短码和以它为前缀的长码，优先保留长码，短码很可能是切分错误。
    unique_tokens = sorted(set(tokens), key=lambda x: (len(x), x))
    kept = []
    removed = []
    for token in unique_tokens:
        if any(longer != token and longer.startswith(token) for longer in unique_tokens):
            removed.append(token)
        else:
            kept.append(token)
    return kept, removed


def parse_model_output(content: str) -> tuple[list, list]:
    """【智能多位数防御引擎】全面兼容3~5位数，只定点清除 0001, 00054 等补零幻觉"""
    content = content.strip()
    # 🎯 允许抓取 3 到 6 位的数字，放开五位数的大门
    raw_numbers = re.findall(r"\b\d{3,6}\b", content)
    
    validated_numbers = []
    for num in raw_numbers:
        num_str = str(num).strip()
        
        # 拦截过长幻觉（6位及以上不要）
        if len(num_str) >= 6:
            continue
            
        # 🎯【4位数校验】：如果是4位数，且以 "00" 开头（如 0001, 0021），说明是模型背数幻觉，剔除
        if len(num_str) == 4 and num_str.startswith("00"):
            continue
            
        # 🎯【5位数校验】：如果是5位数，且以 "00" 开头（如 00005, 00054），属于无意义的补零幻觉，剔除
        # 但如果是正常的 "10054"（以1开头），属于真正的饰品五位数，完美保留！
        if len(num_str) == 5 and num_str.startswith("00"):
            continue
            
        # 正常通过筛选的数据（3位数的 001、080，4位数的 1234，5位数的 10024 等）
        validated_numbers.append(num_str)
        
    if validated_numbers:
        return _filter_prefix_tokens(sorted(list(set(validated_numbers))))
    return [], []


def parse_args():
    if len(sys.argv) <= 1:
        return None

    arg = sys.argv[1].strip()
    if arg in {"-h", "--help"}:
        print("用法: python extract_codes.py [output_folder_name]")
        print("如果不指定输出文件夹，将自动创建一个基于当前时间的子目录。")
        sys.exit(0)

    return arg


def get_output_dir(folder_name: Optional[str] = None) -> str:
    os.makedirs(OUTPUT_BASE_DIR, exist_ok=True)
    if folder_name:
        output_dir = os.path.join(OUTPUT_BASE_DIR, folder_name)
    else:
        output_dir = os.path.join(OUTPUT_BASE_DIR, f"run-{datetime.now():%Y%m%d-%H%M%S}")
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def ask_vision_single(image_path: str, temp: float = 0.1) -> tuple[list, list, str]:
    messages = [
        {"role": "system", "content": "你是一个严谨的游戏数据抓取工具，绝不说任何废话，只输出数字和逗号。"},
        {"role": "user", "content": [{"type": "text", "text": PROMPT}]}
    ]
    b64 = encode_image(image_path)
    ext = os.path.splitext(image_path)[1].lower().lstrip(".")
    if ext == "jpg": ext = "jpeg"
    mime = f"image/{ext}"
    messages[1]["content"].append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

    payload = {"model": MODEL, "messages": messages, "max_tokens": 1000, "temperature": temp}
    proxies = {"http": None, "https": None}

    try:
        resp = requests.post(f"{API_BASE}/chat/completions", json=payload, proxies=proxies, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] 请求物理失败：{e}")
        raise

    data = resp.json()
    content = ((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    kept, removed = parse_model_output(content)
    return kept, removed, content


def log_or_update_md(image_path: str, status: str, raw_output: str = "", output_dir: Optional[str] = None) -> None:
    rel_path = os.path.relpath(image_path, BASE_DIR)
    img_name = os.path.basename(image_path)
    clean_raw = raw_output.replace("\n", " ").replace("\r", "") if raw_output else "无"
    md_log_path = os.path.join(output_dir if output_dir else OUTPUT_BASE_DIR, "数据清洗及错误盘点.md")

    if not os.path.exists(md_log_path):
        os.makedirs(os.path.dirname(md_log_path), exist_ok=True)
        with open(md_log_path, "w", encoding="utf-8") as f:
            f.write("# 📊 游戏图鉴大模型数据清洗及错误盘点看板\n\n")
            f.write("> 💡 本看板实时同步清洗状态。数量过少（漏报）或无数据的图片将被判定为失败并在此盘点。\n\n")
            f.write("## ❌ 待处理/彻底失败列表 (Todo)\n")
            f.write("| 预览图 | 图片名称与相对路径 | 最终大模型原始输出 |\n")
            f.write("| :---: | :--- | :--- |\n\n")
            f.write("## ♻️ 历史修复成功列表 (Done)\n")
            f.write("| 预览图 | 修复的图片路径 | 状态 |\n")
            f.write("| :---: | :--- | :--- |\n")

    with open(md_log_path, "r", encoding="utf-8") as f:
        content_str = f.read()

    was_in_todo = f"`{rel_path}`" in content_str or rel_path in content_str

    if status == "SUCCESS" and not was_in_todo:
        return

    lines = content_str.splitlines()
    new_lines = []
    in_todo = False
    
    todo_row = f"| ![{img_name}]({rel_path}) | `{rel_path}` | {clean_raw} |"
    done_row = f"| ![{img_name}]({rel_path}) | ~~`{rel_path}`~~ | 已被成功修复（通过兼容多位数的严格校验） |"

    already_in_todo = False
    for line in lines:
        if "## ❌ 待处理" in line:
            in_todo = True
        elif "## ♻️ 历史修复" in line:
            in_todo = False
            
        if in_todo and rel_path in line and status == "SUCCESS":
            continue
        if in_todo and rel_path in line and status == "FAILED":
            already_in_todo = True
            
        new_lines.append(line)

    if status in {"FAILED", "WARNING"} and not already_in_todo:
        for idx, line in enumerate(new_lines):
            if "## ♻️ 历史修复" in line:
                new_lines.insert(idx - 1, todo_row)
                break
        else:
            new_lines.append(todo_row)
    elif status == "SUCCESS" and was_in_todo:
        is_already_done = any(rel_path in l for l in new_lines if "~~" in l)
        if not is_already_done:
            new_lines.append(done_row)

    with open(md_log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


def main():
    if not os.path.exists(BASE_DIR): return

    all_dirs = [d for d in os.listdir(BASE_DIR) if os.path.isdir(os.path.join(BASE_DIR, d))]
    if not all_dirs: return

    high_priority = ["袜子","饰品" ]   
    low_priority = ["下装", "鞋子","妆容"]    
    middle_priority = [d for d in all_dirs if d not in high_priority and d not in low_priority]
    categories = [d for d in high_priority if d in all_dirs] + middle_priority + [d for d in low_priority if d in all_dirs]

    print(f"🗂 兼容五位数全新处理顺序: {categories}")
    print(f"🎯 智能多位数洗涤引擎已加载！最低有效数量：{MIN_EXPECTED_COUNT}")
    print("------------------------------------------------------------------")

    folder_name = parse_args()
    output_dir = get_output_dir(folder_name)
    print(f"📁 输出目录: {output_dir}")

    try:
        for cat in categories:
            cat_path = os.path.join(BASE_DIR, cat)
            print(f"\n================ 进入分类目录: 【{cat}】 ================")
            
            sub_dirs = sorted([d for d in os.listdir(cat_path) if os.path.isdir(os.path.join(cat_path, d))])
            if not sub_dirs: continue

            txt_output_path = os.path.join(output_dir, f"{cat}.txt")
            cat_tokens = []
            seen = set()
            
            if os.path.exists(txt_output_path):
                with open(txt_output_path, "r", encoding="utf-8") as f:
                    old_content = f.read().strip()
                    if old_content:
                        cat_tokens = [t.strip() for t in old_content.split(",") if t.strip()]
                        seen = set(cat_tokens)

            for sub_dir in sub_dirs:
                group_path = os.path.join(cat_path, sub_dir)
                print(f"\n📂 正在扫描 ➡️ {cat}/{sub_dir}")
                
                group_images = sorted([
                    os.path.join(group_path, f) for f in os.listdir(group_path)
                    if os.path.splitext(f)[1].lower() in IMAGE_EXTS
                ])
                if not group_images: continue
                
                group_added_count = 0
                failed_images = {}
                
                for img_p in group_images:
                    img_name = os.path.basename(img_p)
                    try:
                        items, removed_items, raw_txt = ask_vision_single(img_p, temp=0.1)
                        
                        if items and len(items) >= MIN_EXPECTED_COUNT:
                            print(f"     📄 {img_name} -> 成功提取(真五位数可过): {items}")
                            if removed_items:
                                detail = f"前缀冲突剔除短码：{','.join(removed_items)} | 原始全文本: {raw_txt}"
                                log_or_update_md(img_p, "WARNING", detail, output_dir=output_dir)
                            else:
                                log_or_update_md(img_p, "SUCCESS", output_dir=output_dir)
                            for token in items:
                                t = str(token).strip()
                                if t and t not in seen:
                                    seen.add(t)
                                    cat_tokens.append(t)
                                    group_added_count += 1
                        else:
                            reason = f"有效数字过少({len(items)}个): {items}" if items else "未捕获到有效数字"
                            print(f"     ⚠️ {img_name} -> 拦截并判定为失败（{reason}）")
                            failed_images[img_p] = f"【清洗拦截】：{reason} | 原始全文本: {raw_txt}"
                    except Exception as e:
                        failed_images[img_p] = f"错误: {str(e)}"
                
                if failed_images:
                    for fail_img_p, first_raw in failed_images.items():
                        fail_img_name = os.path.basename(fail_img_p)
                        try:
                            retry_items, retry_removed, second_raw = ask_vision_single(fail_img_p, temp=0.3)
                            
                            if retry_items and len(retry_items) >= MIN_EXPECTED_COUNT:
                                print(f"     🎉 [复活成功] ➡️ {fail_img_name}: {retry_items}")
                                if retry_removed:
                                    detail = f"前缀冲突剔除短码：{','.join(retry_removed)} | 原始全文本: {second_raw}"
                                    log_or_update_md(fail_img_p, "WARNING", detail, output_dir=output_dir)
                                else:
                                    log_or_update_md(fail_img_p, "SUCCESS", output_dir=output_dir)
                                for token in retry_items:
                                    t = str(token).strip()
                                    if t and t not in seen:
                                        seen.add(t)
                                        cat_tokens.append(t)
                                        group_added_count += 1
                            else:
                                fail_reason = f"二轮有效过少({len(retry_items)}个)" if retry_items else "二轮洗涤后为空"
                                print(f"     ☠️ [确认为错误图] ➡️ {fail_img_name}（{fail_reason}）")
                                log_or_update_md(fail_img_p, "FAILED", f"{first_raw} || [2轮]: {fail_reason} -> {second_raw}", output_dir=output_dir)
                        except Exception as e:
                            log_or_update_md(fail_img_p, "FAILED", f"{first_raw} || [2轮报错]: {str(e)}", output_dir=output_dir)
                
                if cat_tokens:
                    cat_tokens = sorted(list(set(cat_tokens)))
                    with open(txt_output_path, "w", encoding="utf-8") as f:
                        f.write(",".join(cat_tokens))

    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()