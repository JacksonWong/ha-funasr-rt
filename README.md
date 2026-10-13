# ha-funasr-rt — FunASR Realtime STT for Home Assistant

为 Home Assistant Assist 语音管线提供基于 [FunASR](https://github.com/modelscope/FunASR) 模型的实时语音识别（STT）。

## 组成

| 组件 | 说明 |
|---|---|
| `custom_components/funasr_rt/` | **HA 自定义集成**（HACS 安装）：HA 进程内直连识别后端，配置 UI，自动热词 |
| `wyoming-shim/` | 独立 **Wyoming 协议桥**：适用于非 HAOS 部署（WSL/PVE/Docker 挂外部 STT 服务） |

## 识别后端

- **云端**：阿里云百炼 DashScope `fun-asr-realtime`，WebSocket 全流式——边说边识别，语音结束后 ~0.2s 出最终文本（实测尾延迟 190-220ms）
- **本地（v1.1.0 新增）**：自建 [FunASR runtime](https://github.com/modelscope/FunASR) 2pass 服务（WebSocket 10095，Paraformer 流式+离线纠错），语音不出局域网、零 API 费用；添加集成时选“本地”并填服务地址（如 `ws://192.168.31.8:10095`），提交时自动连通性验证。实测 4s 音频端到端 ~5.5s（含尾端离线纠错 ~1.3s）
- 双后端可同时配置为两个集成条目（`FunASR Realtime` / `FunASR Local`），在语音助手管线里随时切换，互为备份

## 特性

- 流式识别；双后端：云端低尾延迟（~0.2s），本地完全离线
- **热词自动同步**：自动收集 HA 设备/区域名（friendly_name + areas）并可附手动词表，维护一张 DashScope 热词表（内容变化才调 API），显著改善设备名识别；不符合百炼格式硬规则的词自动过滤并记日志（含非 ASCII 总长 >15 或纯 ASCII >7 段），失败带 10 分钟退避，识别本身不受影响
- 配置界面填写 API key，提交时在线验证（401 直接拦截）；本地后端提交时验证 WebSocket 连通性
- **本地后端热词**：设备/区域名 + 手动词表以每会话 JSON 直发 runtime（权重 20，contextual 模型），无云端词表依赖
- 并发会话隔离；后端错误/超时以明确的 STT 错误呈现，不挂死语音管线
- zh-Hans / en 双语界面

## 安装（HACS 自定义仓库）

1. HACS → 右上角 ⋮ → **自定义仓库** → 填入本仓库 URL → 类别选 **集成（Integration）**
2. 搜索 “FunASR Realtime STT” → 下载 → 重启 HA
3. 设置 → 设备与服务 → 添加集成 → **FunASR Realtime STT** → 选后端：云端填百炼 API key；本地填 FunASR runtime 地址
4. 设置 → 语音助手 → 你的助手 → 语音转文字 → 选 **FunASR Realtime**

### API Key 注意

- 需要百炼**公网** API key（经典 `sk-` 格式，对 `dashscope.aliyuncs.com` 有效），在百炼控制台获取
- **token-plan / MaaS 专用端点的 key 不可用**（公网 ASR 接口会 401）
- 子业务空间的 key 需同时填写 Workspace ID（`llm-` 开头）
- `fun-asr-realtime` 按量计费

### 本地后端：FunASR runtime 部署

在任意 x86_64 Docker 主机（建议 ≥4 核/8G）运行（镜像/模型均国内源，免代理）：

```bash
mkdir -p ~/funasr/models
docker run -d --name funasr --restart unless-stopped -p 10095:10095 \
  -v ~/funasr/models:/workspace/models \
  registry.cn-hangzhou.aliyuncs.com/funasr_repo/funasr:funasr-runtime-sdk-online-cpu-0.1.13 \
  bash -c 'cd /workspace/FunASR/runtime/websocket/build/bin && exec ./funasr-wss-server-2pass \
    --download-model-dir /workspace/models \
    --model-dir damo/speech_paraformer-large-contextual_asr_nat-zh-cn-16k-common-vocab8404-onnx \
    --online-model-dir damo/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online-onnx \
    --vad-dir damo/speech_fsmn_vad_zh-cn-16k-common-onnx \
    --punc-dir damo/punc_ct-transformer_zh-cn-common-vad_realtime-vocab272727-onnx \
    --itn-dir thuduj12/fst_itn_zh \
    --certfile "" --keyfile ""'
```

首次启动自动从 ModelScope 下载模型（约 2GB）。`--model-dir` 用 contextual 版以支持热词；`--certfile ""` 为明文 ws（仅限局域网）。HA 集成里后端选“本地”，填 `ws://<主机IP>:10095`。

### 隐私说明

云端后端会将语音音频流式发送至阿里云百炼进行识别。本地后端的音频仅在局域网内传输（HA → FunASR runtime），不出网。

## 选项（集成 → 配置选项）

| 选项 | 默认 | 说明 |
|---|---|---|
| model | `fun-asr-realtime` | 识别模型（仅云端后端） |
| hotwords | — | 附加热词，逗号分隔 |
| auto_hotwords | 开 | 自动收集设备/区域名作为热词 |
| vocab_prefix | `hafunasr` | 热词表前缀（仅云端后端；小写字母/数字，<10 字符） |
| final_timeout | 15 | 音频结束后等待最终文本的秒数 |

## wyoming-shim（独立部署方案）

Wyoming 协议 STT 服务，代码同源、久经实测（并发/错误路径/热词），适合把识别服务放在 HAOS 之外的主机（WSL、PVE LXC、Docker）。详见 [`wyoming-shim/README.md`](wyoming-shim/README.md)。

注意：shim 与集成各自使用独立热词表前缀（`wyshim` / `hafunasr`），可共存。

## 要求

- Home Assistant 2026.4+（`SpeechToTextEntity` API；v1.0.1 针对 HAOS 2026.4.4 修复枚举改名并在同版本做属性级校验；对 2026.2 旧枚举名保留回退兼容）
- HAOS 安装集成时自动拉取依赖 `dashscope==1.27.7`

## Roadmap

- [x] 本地 FunASR runtime 后端（WebSocket 10095，2pass Paraformer）— v1.1.0
- [ ] 识别延迟打点与统计
- [ ] WS 失败降级到非实时模型（fun-asr-flash）

## License

MIT
