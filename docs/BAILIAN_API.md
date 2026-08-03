# 阿里云百炼 (Bailian) API 集成指南

本文档旨在为量化交易系统集成阿里云百炼大模型（通义千问 Qwen）提供技术参考。

### 1.1 OpenAI 兼容模式 (通用)
- **Base URL**: `https://dashscope.aliyuncs.com/compatible-mode/v1`
- **环境变量**: `DASHSCOPE_API_KEY`
- **主要模型**: `qwen-max`, `qwen-plus`, `qwen-turbo`

### 1.2 Anthropic 协议适配器 (Claude CLI 专用)
为了让 **Claude Code** (Anthropic CLI) 能直接调用百炼模型，请使用专用适配地址。

- **Base URL**: `https://dashscope.aliyuncs.com/apps/anthropic`
- **环境变量**: `ANTHROPIC_API_KEY` (填入百炼 API KEY)
- **主要模型**:
  - `qwen3.5-plus`: 默认推荐。
  - `qwen3-max`: 支持 **Thinking (思维链)** 模式，适合处理复杂量化代码。
  - `qwen3.5-flash`: 高速、低延迟。


## 2. Python 调用示例

### 2.1 使用 OpenAI SDK (推荐)

```python
import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

completion = client.chat.completions.create(
    model="qwen-max",
    messages=[
        {'role': 'system', 'content': '你是一位资深量化交易员。'},
        {'role': 'user', 'content': '请分析中证500指数在过去5年的基本面变化趋势。'}
    ]
)

### 2.2 使用 Anthropic SDK (适配器模式)

```python
import os
from anthropic import Anthropic

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/apps/anthropic",
)

message = client.messages.create(
    model="qwen3.5-plus",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "请用 Python 写一个简单的均线策略示例。"}
    ]
)

print(message.content[0].text)
```


## 3. 量化应用场景

### 3.1 研报自动化分析
将下载的 PDF 研报或新闻摘要输入百炼，提取关键词、情绪得分（Bullish/Bearish）作为量化因子的补充。

### 3.2 策略逻辑解释
将复杂的 Pandas 选股逻辑转换为人类自然语言，生成策略说明书。

### 3.3 自动化研报
基于回测结果生成的绩效指标（Sharpe, MDD），自动生成周度/月度投资分析报告。

## 4. 注意事项
- **角色限制**: 百炼目前仅支持 `system`, `user`, `assistant` 角色。
- **并发控制**: 请注意百炼 API 的 RPM（每分钟请求数）限制，在大批量处理时建议增加延迟或使用异步调用。
- **数据隐私**: 发送到 API 的数据应在遵守隐私政策的前提下进行。
