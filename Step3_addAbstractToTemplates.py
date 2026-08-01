"""
为模板库中的所有模板添加abstract字段
"""
import json
import os
import sys
import time
import random
from typing import Dict, Any
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, AuthenticationError, BadRequestError
from ExtraFunctions import render_contract_to_plain_text

# 配置OpenAI API
API_KEY = os.getenv("baiLianKey")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
if not API_KEY:
    raise ValueError("环境变量 'baiLianKey' 未设置")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
MODEL_ID = "deepseek-v3.2"
FALLBACK_MODEL_ID = "deepseek-v4-flash"
TEMPERATURE = 0.2
TIMEOUT = 1200
STREAM: bool = False
MAX_RETRIES = 2
BASE_DELAY = 1


# 摘要生成提示词
ABSTRACT_PROMPT_TEMPLATE = """
你是专业合同律师，请为以下合同模板生成一段**高度概括、结构统一、适合向量检索**的核心摘要。
摘要必须包含以下内容，语句通顺、专业简洁，长度控制在 100～200 字：
1. 合同类型
2. 适用场景与目的
3. 签约双方角色
4. 核心业务约定
5. 几条重要且特殊的条款
6. 关键法律约定（违约、解除、争议解决等）
7. 合同复杂度
8. 适用地区
不要格式、不要列表、不要标题，只输出一段连贯通顺的摘要文本。

合同模板内容：
{{contract_text}}
"""

def generate_abstract(contract_text: str) -> str:
    prompt = ABSTRACT_PROMPT_TEMPLATE.replace("{{contract_text}}", contract_text)
    current_model = MODEL_ID
    current_timeout = TIMEOUT

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            response = client.chat.completions.create(
                model=current_model,
                messages=[
                    {"role": "system", "content": "你是专业合同律师，擅长生成合同摘要。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=TEMPERATURE,
                timeout=current_timeout,
                stream=STREAM,
            )
            return response.choices[0].message.content.strip()
        except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as e:
            delay = (BASE_DELAY * (2 ** (attempt - 1))) + random.uniform(0, 1)
            print(f"  临时故障（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s...")
            time.sleep(delay)
            if isinstance(e, APITimeoutError):
                current_timeout = int(current_timeout * 1.5)
                print(f"  超时阈值已调整至 {current_timeout}s")
            if attempt >= 2 and current_model == MODEL_ID:
                print(f"  降级至备用模型 {FALLBACK_MODEL_ID}")
                current_model = FALLBACK_MODEL_ID
            continue
        except (AuthenticationError, BadRequestError) as e:
            print(f"  请求错误（{type(e).__name__}），无需重试，跳过该模板")
            return "<LLM_ERROR>"
        except Exception as e:
            delay = (BASE_DELAY * (2 ** (attempt - 1))) + random.uniform(0, 1)
            print(f"  未知异常（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s...")
            time.sleep(delay)
            continue
    return "<LLM_ERROR>"

def process_templates():
    """
    处理所有模板，为没有abstract字段的模板添加摘要
    """
    template_dir = "template_library"
    
    # 检查模板目录是否存在
    if not os.path.exists(template_dir):
        print(f"错误：模板目录 {template_dir} 不存在")
        sys.exit(1)
    
    # 获取所有JSON文件
    json_files = [f for f in os.listdir(template_dir) if f.endswith('.json')]
    
    if not json_files:
        print(f"错误：模板目录 {template_dir} 中没有JSON文件")
        sys.exit(1)
    
    print(f"找到 {len(json_files)} 个模板文件")
    print("=" * 50)
    
    for file_name in json_files:
        if "all_classify" in file_name: continue
        file_path = os.path.join(template_dir, file_name)
        print(f"处理文件: {file_name}")
        
        try:
            # 读取文件
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # 遍历文件中的所有模板
            updated = False
            for key, template in data.items():
                # 检查是否已有abstract字段
                if "abstract" not in template:
                    print(f"  为模板 {template.get('template_name', key)} 添加abstract...")
                    
                    # 获取合同纯文本
                    standard_content = template.get("standard_template_content", {})
                    if standard_content:
                        plain_text = render_contract_to_plain_text(standard_content)
                        
                        # 生成摘要
                        abstract = generate_abstract(plain_text)
                        if abstract and not abstract.startswith("<LLM_ERROR"):
                            template["abstract"] = abstract
                            updated = True
                            print(f"  已生成摘要: {abstract[:50]}...")
                        else:
                            print(f"  生成摘要失败，跳过该模板")
                    else:
                        print(f"  模板 {template.get('template_name', key)} 缺少standard_template_content")
                else:
                    print(f"  模板 {template.get('template_name', key)} 已存在abstract字段")
            
            # 保存更新后的文件
            if updated:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"  文件 {file_name} 已更新")
            else:
                print(f"  文件 {file_name} 无需更新")
        except Exception as e:
            print(f"  处理文件时出错: {str(e)}")
        
        print()

if __name__ == "__main__":
    process_templates()
