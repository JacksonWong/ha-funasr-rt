# ha-funasr-rt — FunASR Realtime STT for Home Assistant

为 Home Assistant Assist 语音管线提供基于 [FunASR](https://github.com/modelscope/FunASR) 模型的实时语音识别（STT）。

## 组成

| 组件 | 说明 |
|---|---|
| `custom_components/funasr_rt/` | **HA 自定义集成**（HACS 安装）：HA 进程内直连识别后端，配置 UI，自动热词 |
| `wyoming-shim/` | 独立 **Wyoming 协议桥**：适用于非 HAOS 部署（WSL/PVE/Docker 挂外部 STT 服务） |

## 识别后端

- **云端（已实现）**：阿里云百炼 DashScope `fun-asr-realtime`，WebSocket 全流式——边说边识别，语音结束后 ~0.2s 出最终文本（实测尾延迟 190-220ms）
- **本地（规划中）**：FunASR runtime（Paraformer/SenseVoice，WebSocket 10095）。domain 取名 `funasr_rt` 即为云端/本地双后端预留；当前版本选择"本地"会明确提示未实现

## 特性

- 流式识别，低尾延迟
- **热词自动同步**：自动收集 HA 设备/区域名（friendly_name + areas）并可附手动词表，维护一张 DashScope 热词表（内容变化才调 API），显著改善设备名识别；不符合百炼格式硬规则的词自动过滤并记日志（含非 ASCII 总长 >15 或纯 ASCII >7 段），失败带 10 分钟退避，识别本身不受影响
- 配置界面填写 API key，提交时在线验证（401 直接拦截）
- 并发会话隔离；后端错误/超时以明确的 STT 错误呈现，不挂死语音管线
- zh-Hans / en 双语界面

## 安装（HACS 自定义仓库）

1. HACS → 右上角 ⋮ → **自定义仓库** → 填入本仓库 URL → 类别选 **集成（Integration）**
2. 搜索 “FunASR Realtime STT” → 下载 → 重启 HA
3. 设置 → 设备与服务 → 添加集成 → **FunASR Realtime STT** → 填百炼 API key
4. 设置 → 语音助手 → 你的助手 → 语音转文字 → 选 **FunASR Realtime**

### API Key 注意

- 需要百炼**公网** API key（经典 `sk-` 格式，对 `dashscope.aliyuncs.com` 有效），在百炼控制台获取
- **token-plan / MaaS 专用端点的 key 不可用**（公网 ASR 接口会 401）
- 子业务空间的 key 需同时填写 Workspace ID（`llm-` 开头）
- `fun-asr-realtime` 按量计费

### 隐私说明

云端后端会将语音音频流式发送至阿里云百炼进行识别。

## 选项（集成 → 配置选项）

| 选项 | 默认 | 说明 |
|---|---|---|
| model | `fun-asr-realtime` | 识别模型 |
| hotwords | — | 附加热词，逗号分隔 |
| auto_hotwords | 开 | 自动收集设备/区域名作为热词 |
| vocab_prefix | `hafunasr` | 热词表前缀（小写字母/数字，<10 字符） |
| final_timeout | 15 | 音频结束后等待最终文本的秒数 |

## wyoming-shim（独立部署方案）

Wyoming 协议 STT 服务，代码同源、久经实测（并发/错误路径/热词），适合把识别服务放在 HAOS 之外的主机（WSL、PVE LXC、Docker）。详见 [`wyoming-shim/README.md`](wyoming-shim/README.md)。

注意：shim 与集成各自使用独立热词表前缀（`wyshim` / `hafunasr`），可共存。

## 要求

- Home Assistant 2026.4+（`SpeechToTextEntity` API；v1.0.1 针对 HAOS 2026.4.4 修复枚举改名并在同版本做属性级校验；对 2026.2 旧枚举名保留回退兼容）
- HAOS 安装集成时自动拉取依赖 `dashscope==1.27.7`

## Roadmap

- [ ] 本地 FunASR runtime 后端（WebSocket 10095，Paraformer/SenseVoice）
- [ ] 识别延迟打点与统计
- [ ] WS 失败降级到非实时模型（fun-asr-flash）

## License

MIT
