# warsaw（REDMI K90 至尊版）适配进展：上游 v0.2.3 同步 + v104 SIGTRAP/流畅度修复（2026-11-27）

> 严格遵守硬边界：**仅 `fastboot boot`（纯 RAM 启动），不刷写任何持久分区；IPSW 与厂商固件只留本地，不入库。**

## 1. v103 SpringBoard SIGTRAP（SIGNAL 5）与 v93→v102 流畅度退化根因

### 1.1 SIGTRAP 根因（`once-v103`）
- `once-v103` 两次崩溃均在 `libdispatch.dylib`（`x0=0x16` `EINVAL`、`x0=0xf` `KERN_INVALID_NAME`），位于 `xpc_connection_send_message_with_reply` 回调链。
- 根因在 `userland/src/inject_sb26.c`（非 ARC C）：`dispatch_semaphore_t done` 未被异步 block retain。当 `dispatch_semaphore_wait` 超时返回，调用线程立刻 `dispatch_release(done)` 销毁信号量；数毫秒后 `iconservicesagent` 回包触发 `dispatch_semaphore_signal(done)` 踩已释放信号量，触发 `libdispatch` `brk #1`。同时 `sb26_xpc_cache` 缺少互斥锁，存在多线程竞态。

### 1.2 为何 v93 最流畅、v96–v103 变卡
- `once-v93ctl`（`xpc_cold_ms=300`）：`icon_timeout=80, icon_hit=2`，帧间隔 P50 = `204 ms`。
- `once-v102`（`xpc_cold_ms=80`）：`icon_timeout=174, icon_hit=0`，帧间隔 P50 = `265 ms`。`80 ms` 短于 `iconservicesagent` 冷请求响应耗时，导致全部请求超时、0 缓存命中、持续重请，且每次超时都会触发上述信号量 UAF。

### 1.3 v104b 修复（回退基线，commit `64079908`）
- `struct sb26_xpc_wait` 引用计数（`refs=2`）+ 原子状态机（`CAS 0→1` / `exchange 2`），彻底消除信号量 UAF。
- `sb26_xpc_cache` 全路径加 `pthread_mutex_t`，返回 retain 对象；迟到回包仍写入缓存。
- 默认 `xpc_cold_ms` 调回 `200 ms`（`build.sh` 与 `glass.conf` 同步），保留 v104 图标空闲跳过 + 5 轮晚扫。
- CI `45988742937`（`build sb26 dylib`）绿，已配合 `0016` `libredwoodair.dylib`（sha256 `134c61c3ad188cb1`）打包为本地 `payload-v104.bin`。

---

## 2. 上游 `iola1999/hackios-redmi-k90-annibale` v0.2.3（2026-11-27）`warsaw` 纯 RAM 适配

### 2.1 上游兼容性审计结论
对照 `v0.2.3`（内核 `319d6f78`、用户态 `17929ae6`）：
1. **显示与触控分辨率无关**：`MiniIOMobileFramebuffer.cpp` 通过 `fbwidth=1280 fbheight=2772 fbpitch=460` boot-arg 动态配置，`MiniFramebufferFlip.inc` 动态读取 `vinfo.v_width/v_height/v_rowbytes`，`annibale_goodix.c` 启动时读取触控芯片最大坐标并归一化至 `0..65535`。
2. **Loader ABI 兼容**：`identity_v57.h` 与本机 `warsaw` `loader-phone.efi` 0 行差异。保留 `warsaw` 专版 `loader-phone.efi`（`1280×2772`、`oem ramdisk` 支持）。
3. **关键流畅度与图形红利**：
   - Adreno 830 14 档按需调频 + 锁外延迟 `reap`（峰值 `158.7 ms → 3.4 ms`）+ 512 项 Keep 池；
   - Mesa `0016`–`0031`（widget 白块修复、`0029` ring 双重销毁、`0030` BO cache 资格、`0031` Freedreno heap 复用使 `0x42` 分配降 57.2%、背压降 84.1%、照片缩略图与地图建筑/防停转修复）；
   - 原生 GPU `iconservicesagent` + Liquid Glass 图标与半透明 Dock + 原生 PosterBoard 壁纸（不再依赖 CPU PNG fallback）；
   - `MiniFramebuffer` ARM64 四路行哈希 + 10 Hz 静置单核行哈希 + 翻页看门狗 + 背光单写仲裁；
   - 内核自动发布 `MetalPluginName=AnnibaleMetal`，蓝牙 H4 修复，Safari/短信/相机/电话防闪退修复。

### 2.2 本地构建与验证（`tools/build-warsaw-v023.py`）
- 纯 RAM 启动参数：`rootdev=md0`、`cpus=7 bootcpu=7`、`fbwidth=1280 fbheight=2772 fbpitch=460`、`pcie_bdf=bd_p12x.elf`、`fb_hz=120 fb_ca_hz=120 fb_flip=2`，不含 `root_apfs=ufs_darwin` / `ufs_write=1`。
- `root26-v023-warsaw.apfs`（19.79 MB）：`var.skel` 与上游 `13aa8617f7c9d5aa` 逐字节一致，携带 `warsaw` 本地 Wi-Fi（`bd_p12x.elf` / `bdwlan.elf`）与 GPU `gen80000_zap.mbn`，保留本机 `rootfs` 可执行文件齐全的 545 个 LaunchDaemons（含 `com.annibale.*`），`etc/rc` 使用 `exec /annibale/sbin/launchd.ios`。
- `system26-v023-warsaw.apfs`（5.46 GiB，不动原 `system26.apfs`）：内置 `v0.2.3 stage0`、`/annibale` 挂载点、`/System/Library/Extensions/AnnibaleMetal.bundle` 与 `AnnibaleAudio.driver` 符号链接、545-job `xpc/launchd.plist`，以及完整 104,444 条 POSIX manifest，已通过 7-Zip 模式与符号链接核验。
- `payload-v023-warsaw.bin`（40.95 MB ≤ 57.67 MB 上限）：已组装完毕，待真机授权后直接 `boot-once-v2.sh` 验证。
