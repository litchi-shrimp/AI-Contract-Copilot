#!/usr/bin/env python3
"""
LLM 调用模块

description: 管理 LLM 调用和相关配置
所有Agent的配置（包括LLM）都封装在agents/config.py AgentConfig.llm_call
该文件只是服务于测试LLM，不直接在代码中调用。
"""
from pathlib import Path
import time
import sys
from openai import OpenAI, APITimeoutError, APIConnectionError, RateLimitError, InternalServerError, AuthenticationError, BadRequestError
import os
import random
from ..agents.config import LLMError



def llm_call(system_prompt: str, user_prompt: str, api_key_name:str = "DeepSeekKey" , model_id: str = "deepseek-v4-pro", fallback_model_id: str = "deepseek-v4-flash", base_url: str = "https://api.deepseek.com", max_retries: int = 3, stream: bool = False, temperature: float = 0.7, timeout: int = 180, base_delay: int = 1) -> str:
        api_key = os.getenv(api_key_name)
        current_model = model_id
        current_timeout = timeout

        for attempt in range(1, max_retries+1):
            try:
                client = OpenAI(api_key=api_key, base_url=base_url)
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
                    timeout=current_timeout
                )
                full = ""
                if stream:
                    for chunk in response:
                        if not chunk.choices:
                            continue
                        if c := chunk.choices[0].delta.content:
                            print(c, end="", flush=True)
                            full += c
                    print()
                else:
                    full = response.choices[0].message.content
                    usage = response.usage
                    print(f"[Token] 输入: {usage.prompt_tokens}, 输出: {usage.completion_tokens}, 总计: {usage.total_tokens}")
                print(f"[LLM] {current_model} 耗时: {time.time() - start:.1f}s", file=sys.stderr)
                return full.strip()
            except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as e:
                delay = (base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1)
                print(f"⏳ 临时故障（{type(e).__name__}），第 {attempt} 次重试，等待 {delay:.0f}s...", file=sys.stderr)
                time.sleep(delay)
                if isinstance(e, APITimeoutError):
                    current_timeout = int(current_timeout * 1.5)
                    print(f"  超时阈值已调整至 {current_timeout}s", file=sys.stderr)
                if attempt >= 2 and current_model == model_id:
                    print(f"  降级至备用模型 {fallback_model_id}", file=sys.stderr)
                    current_model = fallback_model_id
                continue
            except (AuthenticationError, BadRequestError) as e:
                print(f"❌ 请求错误（{type(e).__name__}），无需重试", file=sys.stderr)
                raise LLMError(f"请求错误（{type(e).__name__}），无需重试")
            except Exception as e:
                print(f"❌ 请求错误（{type(e).__name__}），无需重试", file=sys.stderr)
                raise LLMError(f"请求错误（{type(e).__name__}），无需重试")
        raise LLMError("全部重试失败")


if __name__ == "__main__":
    text = """
    文化墙施工合同条款
    依照《中华人民共和国合同法》、《中华人民共和国建筑法》及其它有关法律、行政法规，就本项工程建设有关事项，遵循平等、自愿、公平和诚实信用的原则，经双方协商达成如下协议：
    第1条   工期及施工内容   
    1.1  本合同工程定于 {合同开工日期} 开工；于  {合同竣工日期} 竣工。工期共{工期天数}天。
    1.2  工程内容包括：{工程内容} 
    第2条   甲方、乙方驻工地代表
    甲方现场负责人姓名： {甲方现场负责人姓名}   ；乙方项目经理姓名： {乙方项目经理姓名} 。
    第3条   甲方义务
    3.1、 甲方派驻施工现场的代表称为工程师或甲方代表，为甲方履行本合同的代表人。甲方代表应由具有相应专业知识的人员担任。
    3.2、双方往来的工程联系单、通知书、验收单、结算单、确认书等函件及会议纪要等，一经甲方代表签字即对甲方具有法律约束力。
    3.3、甲方代表签发的前述函件及指令，一经书面递交乙方工地代表（项目经理或施工员）后即对乙方发生法律约束力。乙方代表对甲方代表发来的函件内容无异议并在函件回执上签署姓名和时间后即对双方发生法律约束力。
    3.4、乙方对甲方代表发出的指令、通知等有异议时，应在{乙方异议提出时限}小时内向甲方书面提出。可采用在回执上签署异议意见的方式，也可另行提出书面异议。甲方在接到异议后不做出答复则视为同意乙方的异议。
    3.5、特殊情况时，甲方代表可向乙方发出口头指令，并应在{甲方口头指令书面确认时限}小时内再予书面确认。乙方认为指令不合理的，可于{乙方对口头指令异议提出时限}小时内提出异议，甲方代表应在收到书面异议后{甲方对异议处理时限}小时内做出是否修改或取消指令的决定，并书面通知乙方。
    3.6、甲方更换驻工地代表，提前书面通知乙方。
    3.7、甲方代表（工程师）的权力自合同生效时产生，至合同终止时终止。
        """
    CURRENT_FILE = Path(__file__).resolve()
    AGENTS_DIR = CURRENT_FILE.parent      # agent/llm/
    AGENT_DIR = AGENTS_DIR.parent         # agent/
    BASE_DIR = AGENT_DIR.parent           # 项目根目录  
    system_prompt = "你是一个专业的复述助手，负责根据用户需求复述文字。原封不动输出文本，文本详见【待复述文本】注意：不能添加任何解释或注释。不要有任何改变。仅输出复述后的文本。"
    user_prompt = f"【待复述文本】：{text}"

    response1 = llm_call(system_prompt, user_prompt,api_key_name="baiLianKey", model_id="deepseek-v3.2",base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",stream=False)
    response2 = llm_call(system_prompt, user_prompt, api_key_name="baiLianKey", model_id="deepseek-v4-flash",base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", stream=False)
    # response3 = llm_call(system_prompt, user_prompt, api_key_name="xiaoaiKey", model_id="gpt-5.2",base_url="https://pro.xiaoai.plus/v1", stream=False)

    print(response1)
    print("="*50)
    print(response2)
    print("="*50)
    # print(response3)

#     判断是否前后一致
    if response1.strip() == text.strip():
        print(f"deepseek-v3.2前后一致")

    else:
        print(f"deepseek-v3.2前后不一致")
    if response2.strip() == text.strip():
        print(f"deepseek-v4-flash前后一致")
    else:
        print(f"deepseek-v4-flash前后不一致")
