#!/usr/bin/env python3
"""装修咨询客服bot demo: 知识库(kb.md) + DeepSeek 问答
用法: renov-bot.py --q "问题"   单次问答
      renov-bot.py              交互模式
"""
import argparse
import json
import os
import urllib.request

KEY = ""
with open(os.path.expanduser("~/.hermes/.env")) as f:
    for line in f:
        if line.startswith("DEEPSEEK_API_KEY="):
            KEY = line.strip().split("=", 1)[1]
BASE = "https://api.deepseek.com"
KB = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kb.md"),
          encoding="utf-8").read()

SYSTEM = f"""你是「装修管家小菲」——一位专业的装修咨询客服,服务深圳 F1501 项目业主。
你的知识库是该项目真实的报价对比、砍价策略、工序清单和主材预算。回答要求:
1. 只依据知识库回答,知识库里没有的明确说"这个我需要查证后再答复"
2. 回答具体、有数字(报价、单价、面积)、可执行
3. 涉及两家公司对比时给出明确建议
4. 语气专业、简洁,用中文

===知识库===
{KB}"""

def ask(q):
    data = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
        ],
        "max_tokens": 800, "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=data,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        out = json.loads(r.read())
    return out["choices"][0]["message"]["content"].strip()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--q", help="单次提问")
    a = p.parse_args()
    if a.q:
        print(ask(a.q))
        return
    print("装修管家小菲上线(输入 exit 退出)\n")
    while True:
        try:
            q = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q or q in ("exit", "quit"):
            break
        print("\n小菲>", ask(q), "\n")

if __name__ == "__main__":
    main()
