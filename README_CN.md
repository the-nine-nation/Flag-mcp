<div align="center">

# 🚩 Flag MCP

<p align="center">
  <a href="./README.md">English</a> | <a href="./README_CN.md">简体中文</a>
</p>

<p align="center">
  <img src="images/hero-banner.jpg" alt="Flag MCP" width="800">
</p>

<p align="center">
  <em>"立下你的 Flag，开启属于你的路线。"</em>
</p>

<p align="center">
  Human-in-the-loop 的 AI 编程交互 —— 因为在每一部精彩的 Galgame 里，选路线的人都是 <strong>你</strong>。
</p>

<p align="center">
  <video src="images/demo.mp4" controls="controls" width="100%" style="max-width: 800px;"></video>
</p>

</div>

---

## ✨ 什么是「Flag」？

在 Galgame 和视觉小说里，**「立 Flag」** 意味着做出一个改变故事走向的关键选择。一个决定，通向完全不同的结局。

**Flag MCP** 把同样的力量带到了 AI 辅助编程中。当你的 AI 助手走到分岔路口时，它不再瞎猜 —— 而是**立起一面 Flag**，等你来选路线。

🎮 **你是主角。** AI 会在每个分支点等待你的指示。

💎 **每个 Flag 都决定路线。** 不再有猜测性的返工。

🚀 **丰富的交互。** 文字、截图、标注 —— 你的全部武器。

这将 AI 编程从"祈祷它能行"变成了 **你手握手柄的互动叙事**。

---

## 🔥 前后对比

| ❌ 没有 Flag MCP | ✅ 有 Flag MCP |
|:------------------:|:----------------:|
| AI 瞎猜 → 写错代码 → 痛苦返工 | AI 立旗 → 你选路线 → 一次写对 |
| 来回好几轮"你到底什么意思？" | 一个结构化对话框，清清楚楚 |
| 焦虑："AI 接下来要干嘛？！" | 自信：每个操作都经过你的确认 |
| 无助的乘客 | 你是路线的决定者 |

---

## 🎯 核心功能

- 🖥️ **暗色主题 UI** — 简洁好看的原生桌面对话框
- ✅ **路线选择** — 结构化的预定义选项（复选框风格）
- 💬 **自由文本** — 当预设路线不够时，自己写剧本
- 📷 **富媒体武器库**
  - 从剪贴板粘贴图片
  - 选择本地文件
  - **截图 + 内置标注器**（矩形、圆形、箭头、画笔、文字、裁剪）
- 🖼️ **提示图片** — AI 可以向你展示图片（本地路径、`file://`、`http(s)://`）
- 🔒 **安全优先** — 远程图片经过验证、大小限制、异步加载
- 🎨 **macOS 优化** — 正确的图标处理和视觉打磨

---

## 📦 安装

### 前置要求

- Python `>= 3.11`
- [`uv`](https://github.com/astral-sh/uv)（推荐）或 `pip`

### 快速开始

```bash
git clone https://github.com/pauoliva/interactive-feedback-mcp.git
cd interactive-feedback-mcp
uv sync
```

---

## ⚙️ 配置

将以下内容添加到 `mcp.json`（Cursor）或 `claude_desktop_config.json`（Claude Desktop）：

```json
{
  "mcpServers": {
    "interactive-feedback": {
      "command": "uv",
      "args": [
        "--directory",
        "/你的路径/interactive-feedback-mcp",
        "run",
        "server.py"
      ],
      "timeout": 900000,
      "autoApprove": ["interactive_feedback"]
    }
  }
}
```

> 💡 **提示**：Cursor 的 `timeout` 单位是**毫秒**（`900000` = 15 分钟）。某些客户端使用秒，请相应调整。

---

## 🚩 `interactive_feedback` 工具

### 参数

| 参数 | 类型 | 描述 |
|------|------|------|
| `message` | `string` | 显示给用户的主要提示/问题 |
| `predefined_options` | `array` | 可选。路线选择项 |
| `message_images` | `array` | 可选。要展示的图片（本地/远程 URL） |

### 返回

- 来自用户的文本反馈
- 可选的图片附件（MCP 图片内容块）

---

## 🧙 进阶技巧

### 推荐的助手规则

将以下内容添加到你的 AI 助手自定义指令中：

```
如果需求不明确，在实现之前调用 interactive_feedback。
尽可能提供预定义选项 —— 给用户清晰的路线选择。
在完成任务之前，再调用一次 interactive_feedback 进行最终确认。
```

### 环境变量

| 变量 | 默认值 | 描述 |
|------|--------|------|
| `INTERACTIVE_FEEDBACK_TIMEOUT_SEC` | `60000` | UI 进程最大生命周期 |
| `INTERACTIVE_FEEDBACK_ICON` | — | 自定义应用图标路径 |
| `INTERACTIVE_FEEDBACK_REMOTE_IMAGE_TIMEOUT_SEC` | `5` | 远程图片获取超时 |
| `INTERACTIVE_FEEDBACK_REMOTE_IMAGE_MAX_BYTES` | `10485760` | 最大远程图片大小（10MB） |

---

## 🛡️ 安全与可靠性

- ✅ 远程图片通过 content-type 验证（`image/*`）
- ✅ 大型负载通过大小上限拒绝
- ✅ 异步获取保持 UI 响应
- ✅ 本地文件仅在显式引用时读取

---

## 🙏 致谢

- **原作者**：Fábio Ferreira ([@fabiomlferreira](https://x.com/fabiomlferreira))
- **增强者**：Pau Oliva ([@pof](https://x.com/pof))
- **灵感来源**：Tommy Tong 的 [interactive-mcp](https://github.com/ttommyth/interactive-mcp)

---

## 📄 许可证

MIT 许可证 —— 随便 fork，随便立旗，随便发。

---

<div align="center">

**🚩 立下你的 Flag，走出你的路线。**

*💜 致那些拒绝在自己的代码库里当 NPC 的开发者。*

</div>
