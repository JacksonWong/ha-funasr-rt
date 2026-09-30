# wyoming-funasr-shim

HA Assist STT ↔ 阿里云百炼 FunASR（fun-asr-realtime，DashScope WebSocket）的 Wyoming 协议桥接。

```
语音卫星 → HA Assist pipeline → [wyoming :10300] 本shim → DashScope WS → 最终文本回传
```

## 阶段状态

- [x] Phase 0: Wyoming 骨架（dummy 后端，HA 侧管道验收通过）
- [x] Phase 1: DashScope fun-asr-realtime 独立验证（test_local.py；owner 真实语音验收：尾延迟 0.19s）
- [x] Phase 2: 合流 + 并发会话隔离 + 超时/错误事件 + 热词（本地 T1-T4 全 PASS，生产已切真实后端）
- [x] Phase 3: 本机托管 = systemd user service（开机自启+崩溃自拉起）；Dockerfile/compose 已交付（PVE 迁移备用）
- [ ] Phase 4（可选）: WS 失败降级 fun-asr-flash、延迟打点

## 运行

```bash
# 环境（uv）
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.txt

# 生产运行（systemd user service：开机自启 + 崩溃自拉起）
# unit: ~/.config/systemd/user/wyoming-funasr-shim.service（linger 已开）
systemctl --user status wyoming-funasr-shim        # 状态
journalctl --user -u wyoming-funasr-shim -f        # 实时日志
systemctl --user restart wyoming-funasr-shim       # 改代码/改.env后重启

# 手动前台运行（调试）
.venv/bin/python server.py --uri tcp://0.0.0.0:10300 --debug

# 本地回环测试（server 运行中；完整测试矩阵见下文「测试」）
.venv/bin/python test_client.py --wav samples/hello_world.wav --expect 阿里巴巴
```

## HA 接入（Phase 0 验收）

1. 本机 WSL 为 **mirrored 网络模式**（WSL 与 Windows 共享局域网 IP，当前 192.168.31.47）。
   LAN→WSL 的入站默认被 **Hyper-V 防火墙**拦截，需在 Windows **管理员 PowerShell** 放行一次：

   ```powershell
   New-NetFirewallHyperVRule -Name "wyoming-funasr-shim" -DisplayName "Wyoming FunASR shim TCP 10300" -Direction Inbound -Protocol TCP -LocalPorts 10300 -Action Allow -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
   ```

   备注：Windows 宿主机自身用 `127.0.0.1:10300` 可直达 shim；宿主机经局域网 IP 访问 WSL 服务需另开 `hostAddressLoopback`（HA 场景不需要）。NAT 模式才需要 netsh portproxy，本机不适用。
2. HA：设置 → 设备与服务 → 添加集成 → Wyoming Protocol → host=192.168.31.47, port=10300
3. 语音助手配置里把 STT 换成 `fun-asr-realtime`（zh-CN）
4. 对卫星说话，STT 输出应为真实语音内容（`FUNASR_BACKEND=dummy` 调试模式则固定回「测试成功」）

## 配置（.env 或环境变量，env 优先于 .env）

- `DASHSCOPE_API_KEY`（必填）：百炼经典 sk- 35 位 key；**token-plan MaaS key 不可用**（公网语音 API 401）
- `DASHSCOPE_WORKSPACE`：业务空间 ID（仅子空间 key 需要）
- `FUNASR_MODEL`（默认 fun-asr-realtime）
- `FUNASR_HOTWORDS`：静态热词，逗号分隔，与 HA 自动下发的设备词表合并
- `FUNASR_VOCAB_PREFIX`（默认 wyshim）：热词表前缀（数字+小写字母，<10 字符）
- `FUNASR_FINAL_TIMEOUT`（默认 15）：AudioStop 后等待最终结果的秒数
- `FUNASR_BACKEND`（默认 dashscope）：`dummy` 用于 HA 侧隔离调试

## 热词

- HA 每次会话通过 `Transcribe.transcript_names/terms` 自动下发管道词表（设备/区域名）
- shim 维护一张共享 DashScope 热词表（前缀 wyshim），内容变化才调 API 更新；状态缓存 `.vocab_state.json`，首跑自动收养同前缀已有表
- 热词失败只降级为无热词，不影响识别会话

## 测试（test_client.py）

```bash
# T1 单路真实转写
.venv/bin/python test_client.py --uri tcp://127.0.0.1:10300 --wav samples/hello_world.wav --expect 阿里巴巴
# T2 3 路并发隔离
.venv/bin/python test_client.py --uri tcp://127.0.0.1:10300 --wav samples/hello_world.wav --expect 阿里巴巴 --concurrent 3
# T3 热词（需含设备名语音的 wav）
.venv/bin/python test_client.py --uri tcp://127.0.0.1:10300 --wav X.wav --expect 空气净化器 --hotwords "空气净化器,客厅灯"
# T4 错误路径：坏 key 起服务，期望 Error 事件而非挂死
DASHSCOPE_API_KEY=*** .venv/bin/python server.py --uri tcp://127.0.0.1:10302 &
.venv/bin/python test_client.py --uri tcp://127.0.0.1:10302 --wav samples/hello_world.wav --expect-error
```

## Docker（PVE / 独立主机迁移备用）

- `Dockerfile` + `docker-compose.yml` 已交付：`restart: unless-stopped`、`env_file: .env` 注入密钥、热词状态经 `FUNASR_STATE_FILE` 持久化到命名卷
- 本机 WSL 未装 docker engine / 未开 Docker Desktop 集成，产物**未经 build 实测**；迁移时目标机 `docker compose up -d --build` 即可
- HA 侧只需把 Wyoming 集成的 host 改成新机器 IP

## 备注

- `requires_external_vad=True`：断句由卫星 VAD 负责（AudioStop），shim 不做端点检测
- 音频统一经 AudioChunkConverter 转 16k/16bit/mono 后喂 DashScope
- 已知 SDK bug（dashscope 1.27.7）：`RecognitionResult.__str__` 崩溃（缺 headers），错误提取必须走 status_code/code/message 属性
- 运维教训：`pgrep/pkill -f` 的模式若在本脚本其他行原文出现，会误杀 exec 会话自身；用变量间接引用或分开执行
