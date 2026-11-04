# AMC1306 数据手册依据（建模出处）

本工程的比特流模型不是凭空拍的，下面每条都对应 TI 数据手册 SBAS734C 的原文。
所有引文取自 alldatasheet.co.nz 对 SBAS734C（2020-01 修订版，45 页）的 HTML 转写，
因为 `ti.com/lit/ds/symlink/amc1306m25.pdf` 对抓取工具返回 `application/pdf`，无法直接提取文字。
若你手上有 PDF，建议按页码复核一遍再对外引用。

> 注意：SBAS734C 里 "Detailed Description" 是第 8 章（8.3.x / 8.4.x），第 7 章是
> Specifications。网上大量二手资料仍按旧版的 7.3.1 / 7.4.2 编号引用。

## 1. 1 的密度 vs 差分输入电压（核心）

第 25 页，§8.3.4 *Digital Output*：
<https://www.alldatasheet.co.nz/html-pdf/2215916/TI2/AMC1306M05DWV.A/1918/25/AMC1306M05DWV.A.html>

> "A differential input signal of 0 V ideally produces a stream of ones and zeros that are high
> 50% of the time. A differential input of 250 mV (for the AMC1306x25) or 50 mV (for the
> AMC1306x05) produces a stream of ones and zeros that are high 89.06% of the time. With 16 bits
> of resolution, that percentage ideally corresponds to the code 58368. A differential input of
> –250 mV (–50 mV for the AMC1306x05) produces a stream of ones and zeros that are high 10.94% of
> the time and ideally results in code 7168 with 16-bit resolution."

- 16 位码：`7168 / 32768 / 58368`（对应 −FS / 0 / +FS）。
- **常见说法"10% / 90%"对本器件是错的**，实际是 10.94% / 89.06%。
- 由这三个数可以反解出（手册里的 Equation 1 是图片，抓不到文字）：

  ```
  d = 0.5 + VIN / (2 * VClipping)      VClipping = 320 mV (x25) / 64 mV (x05)
  ```

  校验：0.5 + 250/640 = 0.890625 → ×65536 = 58368 ✔
        0.5 − 250/640 = 0.109375 → ×65536 = 7168 ✔

- 注意满量程 FSR（±250 mV）**不等于**钳位点（±320 mV）。FSR/VClipping = 0.78125，
  所以到达 ±250 mV 时只用了 16 位码的 ±25600，不是 ±32767。

## 2. 过载行为是 127+1，不是干净的 0/1 轨

第 25 / 26 页，§8.4.2 *Output Behavior in Case of a Full-Scale Input*：
<https://www.alldatasheet.co.nz/html-pdf/2215916/TI2/AMC1306M05DWV.A/1995/26/AMC1306M05DWV.A.html>

> "If a full-scale input signal is applied to the AMC1306 (that is, VIN ≥ VClipping), the device
> generates a single one or zero every 128 bits at DOUT, depending on the actual polarity of the
> signal being sensed, as shown in Figure 54. In this way, differentiating between a missing AVDD
> and a full-scale input signal is possible on the system level."

> "The output of the modulator clips with a stream of only zeros with an input less than or equal
> to –320 mV (–64 mV for the AMC1306x05) or with a stream of only ones with an input greater than
> or equal to 320 mV (64 mV for the AMC1306x05). In this case, however, the AMC1306 generates a
> single 1 (if the input is at negative full-scale) or 0 every 128 clock cycles to indicate proper
> device function"

Figure 54 标注：`VIN ≥ ±320 mV`、`127 CLKIN Cycles`、`Test Pattern`。

→ 生成器对过载段输出 "127 个同电平位 + 1 个反向位"，而不是全 0 / 全 1。
→ 仿真里如果 DUT 用固定 128 周期窗，会看到 ±(127/128 − 0.5)×65536 ≈ ±32256 的码值，
  所以 testbench 对过载段只检查"超出 ±250 mV 满量程"，不比对具体码。

失败安全（对照，§8.4.1）：AVDD 丢失 → 恒 0 流；VCM 过压 → 恒 1 流。

## 3. 时钟与时序

第 12 页，§7.11 *Switching Characteristics*：
<https://www.alldatasheet.co.nz/html-pdf/2215916/TI2/AMC1306M05DWV.A/917/12/AMC1306M05DWV.A.html>

| 参数 | 条件 | 最小 | 典型 | 最大 |
|---|---|---|---|---|
| fCLKIN | 4.5 V ≤ AVDD ≤ 5.5 V | 5 MHz | — | 21 MHz |
| fCLKIN | 3.0 V ≤ AVDD ≤ 5.5 V | 5 MHz | — | 20 MHz |
| tHIGH / tLOW | | 20 ns | 25 ns | 120 ns |
| tH  DOUT 保持 | CLOAD = 15 pF | — | — | 3.5 ns（保持时间） |
| tD  CLKIN↑ → DOUT 有效 | CLOAD = 15 pF | — | — | 15 ns（有效延迟） |

- **没有占空比百分比指标**，只有 tHIGH/tLOW 的绝对上下限。
- 手册没有 typ fCLKIN 一说；"20 MHz" 只是电特性表的测试条件。
- 因此 256 MHz 不可能直接驱动 CLKIN：本工程用 16 分频得到 16 MHz（在 20 MHz 上限内留了 20% 余量）。
- DOUT 由 CLKIN 上升沿发出，本 TB 在 CLKIN **下降沿**采样（半周期后），并在上升沿加 15 ns 的 tD 延迟，
  与手册时序图一致。

## 4. 抽取滤波器：sinc3、OSR = 256

第 10 / 15 页（典型特性表头与电特性条件）：

> "Typical Characteristics at AVDD = 5 V, DVDD = 3.3 V, AINP = –50 mV to 50 mV (AMC1306x05) or
> –250 mV to 250 mV (AMC1306x25), AINN = AGND, fCLKIN = 20 MHz, and sinc3 filter with OSR = 256
> (unless otherwise noted)"

- 这是 TI 自己的表征条件，**不是**"推荐 OSR"的强制要求（手册里没有推荐表）。
- 20 MHz / 256 = 78.125 kSPS；本工程 16 MHz / 256 = 62.5 kSPS。
- 调制器是二阶（§8.3.2 "a second-order, switched-capacitor, feed-forward ΔΣ modulator"），
  输入斩波频率 fCLKIN/32（§8.3.1），所以生成器里加了 32 周期的小纹波。

## 5. 直流精度（AMC1306x25，用于设定噪声/失调模型）

第 10 页，§7.10：<https://www.alldatasheet.co.nz/html-pdf/2215916/TI2/AMC1306M05DWV.A/763/10/AMC1306M05DWV.A.html>

| 项 | 最小 | 典型 | 最大 |
|---|---|---|---|
| 失调误差 EO（25 °C，输入短接） | −100 µV | ±4.5 µV | 100 µV |
| 失调温漂 TCEO | −1 | ±0.15 | 1 µV/°C |
| 增益误差 EG（25 °C） | −0.2 % | ±0.005 % | 0.2 % |
| 增益温漂 TCEG | −40 | ±20 | 40 ppm/°C |
| 钳位 VClipping | | ±320 mV | |
| 线性满量程 FSR | −250 mV | | 250 mV |
| SNR / SINAD（1 kHz） | 82 / 81.9 dB | 86 / 85.7 dB | |
| DNL / INL（16 位） | −0.99 / −4 LSB | | 0.99 / 4 LSB |

生成器用 `--offset-uv 4.5`、`--noise-uv 12`、`--chop-uv 10`，与上表的典型值一致。

## 6. 无法确认的部分（明确标注）

- 手册 Equation 1（密度公式）是图片，未能取到文字；本工程用的是上文反解式，已用 58368/7168 校验。
- "Test Pattern"（tISTART 期间 DOUT 输出什么）手册只给了图注，**未说明内容**，模型里不模拟。
- AMC1306x05 的失调/增益行（第 8 页）本轮未抓取，`--part m05` 时噪声参数沿用了 x25 的量级，
  仅作为激励，不用于精度评估。
