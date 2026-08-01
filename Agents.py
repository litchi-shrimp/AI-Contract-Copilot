"""
智能体
目前包含两个智能体LLM：
1. 合同结构抽取Agent
2. 元数据抽取Agent
"""
from unittest import result
import json
import time
import os
import random
import textwrap
from typing import List, Dict, Any, Optional
from openai import OpenAI
import concurrent.futures
from Prompts import *
from ExtraFunctions import extract_json_from_response, ReviewTool, render_contract_to_plain_text
from datetime import datetime, timedelta
from openai import APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, AuthenticationError, BadRequestError

# ==================== 全局统一 LLM 配置 ====================
API_KEY = os.getenv("baiLianKey")
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
if not API_KEY:
    raise ValueError("环境变量 'baiLianKey' 未设置")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
MODEL_ID = "deepseek-v4-flash"
TEMPERATURE = 0.2
TIMEOUT = 5000
STREAM: bool = False
MAX_RETRIES = 1
RETRY_DELAY_SECONDS = 10  # 每次重试之间等待3秒

def llm_call(system_prompt: str, user_prompt: str, stream: bool = False, temperature: float = TEMPERATURE, max_retries: int = MAX_RETRIES, base_delay: int = 1, timeout: int = TIMEOUT, model_id: str = MODEL_ID, fallback_model_id: str = "deepseek-v4-flash", base_url: str = BASE_URL) -> str:
    current_model = model_id
    current_timeout = timeout

    for attempt in range(1, max_retries + 1):
        try:
            api_key = os.getenv("baiLianKey")
            client = OpenAI(api_key=api_key, base_url=base_url, timeout=current_timeout)
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
            start = time.time()
            response = client.chat.completions.create(
                model=current_model,
                messages=messages,
                stream=stream,
                temperature=temperature,
            )
            full = ""
            if stream:
                for chunk in response:
                    if c := chunk.choices[0].delta.content:
                        print(c, end="", flush=True)
                        full += c
                print()
            else:
                full = response.choices[0].message.content
                usage = response.usage
                print(f"[Token] 输入: {usage.prompt_tokens}, 输出: {usage.completion_tokens}, 总计: {usage.total_tokens}")
            print(f"[LLM] {current_model} 耗时: {time.time() - start:.1f}s")
            return full.strip()

        except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as e:
            delay = (base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
            print(f"⏳ 临时故障（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s...")
            time.sleep(delay)
            if isinstance(e, APITimeoutError):
                current_timeout = int(current_timeout * 1.5)
                print(f"  超时阈值已调整至 {current_timeout}s")
            if attempt >= 2 and current_model == model_id:
                print(f"  降级至备用模型 {fallback_model_id}")
                current_model = fallback_model_id
            continue

        except (AuthenticationError, BadRequestError) as e:
            print(f"❌ 请求错误（{type(e).__name__}），无需重试")
            return f"<LLM_ERROR: {type(e).__name__}>"

        except Exception as e:
            print(f"❌ 请求错误（{type(e).__name__}），无需重试")
            return f"<LLM_ERROR: {type(e).__name__}>"
    return f"<LLM_ERROR: 全部重试失败>"



# ==================== Agent 1：合同结构化抽取Agent ====================
class ExtractContractStructureAgent:
    def __init__(self, contract_txt: str):  
        """
        该Agent负责从给定合同中抽取结构化信息，返回JSON格式的结构化信息清单。
        """
        self.contract_txt = contract_txt
        self.system_prompt = build_contract_system_prompt()
        self.user_prompt = build_contract_user_prompt(self.contract_txt)

    def extract(self) -> str:
        max_count = 3
        count = max_count
        while count > 0:
            count -= 1
            resp = llm_call(self.system_prompt, self.user_prompt, stream=STREAM)
            text = extract_json_from_response(resp)
            text = json.loads(text)
            template_text = render_contract_to_plain_text(text["标准化模板文本"])
            placeholder_list = text.get("占位符清单", [])
            # 开始调用审查工具
            review_result = ReviewTool()._execute(template_text, placeholder_list)
            print(review_result)
            if review_result == "接受":
                print("抽取成功，并且已通过审查")
                return text
            else:
                print(f"抽取失败，需要重新抽取，失败次数：{count}/{max_count}")
                print(review_result)
                self.user_prompt +=  "\n" + review_result
        return """
            "标准化模板文本": "",
            "占位符清单": []
            "结构化信息清单": []
            }
            """
    

class MetadataContractExtractionAgent:
    def __init__(self, contract_txt: str):
        """
        独立元数据抽取：自动识别 contract_type, party_type, scene, complexity, region 等所有标签
        不修改合同文本，只做识别
        """
        METADATA_CONTRACT_SCHEMA = {
            "type": "object",
            "properties": {
                "contract_type": {"type": "string"},
                "party_type": {"type": "string"},
                "scene": {"type": "string"},
                "complexity": {"type": "string"},
                "region": {"type": "string"},
            },
            "required": ["contract_type", "party_type", "scene", "complexity", "region"]
        }
        self.contract_txt = contract_txt
        self.system_prompt = self.build_metadata_system_prompt()
        self.user_prompt = self.build_metadata_user_prompt()
        self.metadata_schema = METADATA_CONTRACT_SCHEMA

    def build_metadata_system_prompt(self):
        return textwrap.dedent("""
        你是专业的合同元数据自动分析专家。
        你只做一件事：从合同正文里自动识别并输出合同的元数据标签，严格按指定JSON输出，不输出任何多余内容。
        contract_type需要从我给你的"合同类型清单"中选择最符合的合同类型，只能选择一个,禁止自创类别。
        其他字段可以匹配合同内容，给你的示例仅供参考。
        """).strip()

    def build_metadata_user_prompt(self):
        with open("./template_library/all_classify.json", "r", encoding="utf-8") as f:
            classify = json.load(f)
        return textwrap.dedent("""
        请分析以下合同文本，自动抽取元数据：

        要求识别：
        1. contract_type：从我给你的"合同类型清单"中选择最符合的合同类型，只能选择一个,禁止自创类别。
           注意：请已知递归寻找到最细粒度的类别(级别依次用.隔开)，例如
           例如"建设工程合同": {"勘察合同": {"ID": "Construction_Survey"}}
           请输出"建设工程合同.勘察合同"，而不是"建设工程合同"或"勘察合同"。
        2. party_type：示例：个人-个人 / 个人-企业 / 企业-企业 / 企业-个人... 当然如果有其他更合适的类型选择，也请输出类型。
        3. scene：示例：居住 / 商铺 / 办公 / 厂房 / 民间借贷 / 金融借贷 / 货物买卖 / 服务提供 / 运输...当然如果有其他更合适的场景，也请输出类型。
        4. complexity：示例：简易 / 标准 / 复杂 / 律所版...当然如果有其他更合适的类型选择，也请输出类型。
        5. region：示例：通用 / 北京 / 上海 / 广东 / 江苏 / 浙江 / 深圳 / 其他...当然如果有其他更合适的地区，也请输出类型。

        输出严格JSON格式，不要解释，必须严格按照指定JSON输出。
        JSON格式：{self.metadata_schema}
        """) .strip() + f"\n合同文本：\n{self.contract_txt}\n合同类型清单：\n{classify}"

    def extract_metadata(self):
        # 调用你的LLM）
        resp = llm_call(self.system_prompt, self.user_prompt, stream=STREAM)
        json_str = extract_json_from_response(resp)
        metadata = json.loads(json_str)
        print(metadata)
        return metadata