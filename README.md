# AMC1306 采样模块 testbench（Vivado 2022.2 / xsim）

针对 `clk = 256 MHz → CLKIN 16 MHz → AMC1306M25 (DOUT 1-bit ΔΣ) → sinc3 OSR=256
→ 62.5 kSPS → 16 位有符号码` 这条链路的**自校验** testbench，激励用的是按
数据手册模型生成的**真实比特流**（不是随机 0/1，也不是方波）。

```
amc1306_tb/
├─ README.md                     ← 本文件
├─ docs/spec_notes.md            ← 每条建模依据的数据手册原文 + 页码链接
└─ sim/
   ├─ gen/gen_bitstream.py       ← 比特流生成器（可重跑、可改参数）
   ├─ gen/golden_cic.py          ← RTL 定点运算的逐位等价模型（验证用）
   ├─ data/
   │   ├─ scenario.mem           ← 12 段 × 2048 bit 的测试流（TB 直接读这个）
   │   ├─ segments/*.mem         ← 同样内容按电平拆开的单段文件
   │   ├─ scenario_params.vh     ← 段长/段数/位深（TB include）
   │   ├─ scenario_table.vh      ← 每段的 Vin / 理想码 / 类型（TB include）
   │   └─ scenario_meta.csv      ← 生成时自校验的结果表（人看）
   ├─ rtl/amc1306_sample_stub.v  ← DUT 参考实现（换掉它就跑你自己的 RTL）
   ├─ tb/tb_amc1306_sample.v     ← testbench
   └─ scripts/
       ├─ run_xsim.bat           ← 命令行一键跑
       └─ build_project.tcl      ← 生成可打开的 Vivado 工程并跑一次
```

---

## 1. 快速开始

### 方式 A：命令行（最快）

```bat
call C:\Xilinx\Vivado\2022.2\settings64.bat
cd /d <本目录>\sim\scripts
run_xsim.bat
```

脚本会编译 → 精化 → 仿真，并把 `data/scenario.mem` 的绝对路径通过
`-testplusarg MEM=...` 传给 TB（所以 xsim 的临时运行目录在哪都不影响读文件）。

### 方式 B：Vivado GUI

```bat
vivado -mode batch -source <本目录>\sim\scripts\build_project.tcl
```

工程生成在 `amc1306_tb/../vivado_amc1306/`，里面已经配好 `include_dirs`、
顶层 `tb_amc1306_sample`、`xsim.simulate.runtime = all` 和 MEM plusarg，
直接打开 `.xpr` 点 Run Simulation 即可。只想建工程不跑：
`... -tclargs -nolaunch`。

### 期望输出

```
 seg  name                   Vin[mV]   ideal   measured  max|err|  n  fail
   0  -1.00 FS               -250.0  -25600     -25600         0   4     0
   1  -0.75 FS               -187.5  -19200     -19200         0   4     0
   ...
   8  +1.00 FS                250.0   25600      25600         0   4     0
   9  +clip 320mV             320.0   32767      32256         0   4     0
  10  -clip 320mV            -320.0  -32768     -32256         0   4     0
  11  0 + 100uV offset         0.1      10         10          0   4     0
 checks            : 50
 value errors      : 0
 worst |err|       : 0 LSB (tolerance 8)
 en gating         : 0 violations
 reset             : 0 violations
 RESULT: PASS
```

（每段 8 个输出样点，前 4 个用于 sinc3 建立期，后 4 个参与比对；过载段 `n` 为 4，
"measured" 是 32256 而不是 32767 是正常的——127/128 的密度解出来就是这个码。）

---

## 2. 接口契约

TB 按下面的端口例化 `amc1306_sample`（**这是本工程唯一需要你确认的地方**）：

| 端口 | 方向 | 位宽 | 含义 |
|---|---|---|---|
| `clk` | in | 1 | 系统时钟 256 MHz |
| `rst_n` | in | 1 | 异步复位，低有效 |
| `en` | in | 1 | 1 = 采集使能；0 = `valid` 拉低、`data` 保持 |
| `bit_in` | in | 1 | AMC1306 DOUT |
| `valid` | out | 1 | 每个输出样点一个 `clk` 脉冲（62.5 kSPS） |
| `data` | out | 16 | **有符号二进制补码**，0 对应 0 A |

隐含约定（如果你的实现不一样，见 §5 的修改点）：

- DUT 内部把 `clk` 16 分频产生 CLKIN，计数由 `rst_n` 清零，CLKIN 上升沿出现在计数为 0 时；
  TB 用同样的约数复制一份 CLKIN 去驱动比特流模型，两边天然对齐。
- 输出码定义：`code = (ones_density − 0.5) × 65536`，即 ±250 mV 满量程对应 ±25600。
  若你的 DUT 把满量程标定到 ±32767，只改 `EXP_NUM/EXP_DEN` 两个 localparam（§5）。

---

## 3. 比特测试数据是怎么来的

生成器 `sim/gen/gen_bitstream.py` 不是"随机 0/1"，它按 SBAS734C 建了一个二阶单环
ΔΣ 调制器，逐 CLKIN 输出 1 bit：

| 项 | 取值 | 依据 |
|---|---|---|
| 密度公式 | `d = 0.5 + VIN/(2×320 mV)` | 手册 p25 §8.3.4，`0.890625×65536 = 58368` 校验 |
| ±250 mV 密度 | 10.94 % / 89.06 % | 原文照抄，**不是 10/90** |
| 过载 | 127 个轨位 + 1 个反向位 | p26 §8.4.2 + Fig.54 "127 CLKIN Cycles" |
| 噪声 | 12 µV rms 输入折算 | SNR 86 dB 典型值反推 |
| 失调 | 4.5 µV | EO 典型值 |
| 斩波纹波 | 10 µV @ fCLKIN/32 | p22 §8.3.1 输入斩波频率 |

12 段测试流（每段 2048 bit = 8 个 sinc3 窗口）：

| 段 | 电平 | Vin | 密度 | 理想码 | 检查方式 |
|---|---|---|---|---|---|
| 0 | −1.00 FS | −250 mV | 10.94 % | −25600 | ±8 LSB |
| 1 | −0.75 FS | −187.5 mV | 20.70 % | −19200 | ±8 LSB |
| 2 | −0.50 FS | −125 mV | 30.47 % | −12800 | ±8 LSB |
| 3 | −0.25 FS | −62.5 mV | 40.23 % | −6400 | ±8 LSB |
| 4 | 0 | 0 mV | 50.00 % | 0 | ±8 LSB |
| 5 | +0.25 FS | +62.5 mV | 59.77 % | +6400 | ±8 LSB |
| 6 | +0.50 FS | +125 mV | 69.53 % | +12800 | ±8 LSB |
| 7 | +0.75 FS | +187.5 mV | 79.30 % | +19200 | ±8 LSB |
| 8 | +1.00 FS | +250 mV | 89.06 % | +25600 | ±8 LSB |
| 9 | 过载 | +320 mV | 99.22 % | — | 只要求 > +24000 |
| 10 | 过载 | −320 mV | 0.78 % | — | 只要求 < −24000 |
| 11 | 零漂移 | +100 µV | 50.00 % | +10 | ±8 LSB |

生成器自己会用**和 RTL 逐位一致的 CIC 参考模型**把这些比特解回码，和理想值比对，
结果写在 `data/scenario_meta.csv`：**所有稳态窗口误差 0 LSB**（最大 1 LSB 出现在
带噪声的零点段）。也就是说，激励本身的精度远高于 8 LSB 的判据，判据留的余量是给
DUT 的实现差异（舍入、截位、增益/失调修正）用的。

想换电平/换器件/换长度：

```bat
python gen_bitstream.py --part m25 --seg-bits 2048 --seed 7
python gen_bitstream.py --part m05            # ±50 mV、钳位 ±64 mV
python gen_bitstream.py --noise-uv 0 --chop-uv 0   # 干净流，用于排查 DUT 自身问题
```

重跑后 `scenario.mem` 和两个 `.vh` 会一起更新，TB 不用改（段数、段长、理想码都从 `.vh` 来）。

---

## 4. TB 都检查了什么

1. **数值正确性**：每个 `valid` 的 `data` 与理想码比，线性段判据 ±8 LSB（`TOL_LSB`）。
2. **跳变建立**：每段电平切换后丢弃前 4 个样点（`SETTLE`，sinc3 三阶梳状需要 3 个窗口建立）。
3. **过载行为**：±320 mV 段只要求码值超出 ±250 mV 满量程，允许 DUT 自己决定饱和方式
   （钳到 ±32767 或输出 ±32256 都算通过）。
4. **输出速率**：每段 `valid` 数量应为 8（±1），能抓出 OSR 配错、多打/漏打 `valid`。
5. **`en` 门控**：中途拉低 `en` 600 个 CLKIN 周期，检查期间 `valid` 必须为低；
   重新使能后继续比对（跳过 4 个样点）。
6. **复位**：复位期间 `valid` 必须为低。
7. **看门狗**：`BIT_DEPTH×DIV×3` 个 clk 内没跑完就报超时，防止死等。

---

## 5. 换成你自己的 DUT

1. 删掉 `sim/rtl/amc1306_sample_stub.v`（或从仿真源里移除），
   把你的 `amc1306_sample.v` / `.vhd` 加进来，**模块名和端口名保持一致**。
2. 如果你的模块有自己的 CLKIN 输出（例如 `clk_out`），把 TB 里
   `wire clkin = ~div_cnt[3];` 换成接你的 `clk_out`，并删掉 `div_cnt` 那段——
   这样时钟相位完全一致，比特流的 tD 关系才严格成立。
3. 如果你的满量程标定不是 ±25600：

   ```verilog
   localparam integer EXP_NUM = 32767;   // ±250 mV 对应的码
   localparam integer EXP_DEN = 25600;
   ```

4. 如果容差不够/太松：改 `TOL_LSB`（默认 8）。噪声大的实现可以放到 20~30；
   想验"纯算法"就把生成器的 `--noise-uv 0 --chop-uv 0` 跑一版。
5. 其它可调：`DIV`（分频比）、`OSR`（抽取比）、`SETTLE`、`TD_PS`（tD 建模值）。

---

## 6. 排错

| 现象 | 原因 / 处理 |
|---|---|
| `ERROR: cannot open the bitstream file` | 没传 `MEM` plusarg，或路径有空格没加引号。用 `run_xsim.bat`，或手动 `-testplusarg "MEM=D:\...\scenario.mem"` |
| 编译报 `scenario_params.vh` 找不到 | 没把 `sim/data` 加进 include 目录（GUI：Simulation Settings → Compilation → include dirs） |
| 所有段都差固定值（±25600 之类） | `EXP_NUM/EXP_DEN` 或 DUT 的满量程定义不对，见 §5.3 |
| 只有前几段对、后面错位 | DUT 的 `valid` 速率不是每 256 个 CLKIN 一次，看 report 里 "valid output rate" |
| `en=0` 时 `valid` 仍为高 | DUT 没做门控，或 `en` 打拍后晚了一拍（TB 允许 1 拍宽限） |
| 最坏误差在 ±300 LSB 量级 | 典型是比对窗口错位/没跳过建立期：把 `SETTLE` 加到 6 |

---

## 7. 验证状态（请先看这条）

- ✅ **比特数据已在本机用 Python 全量校验**：密度、逐窗口 sinc3 解码、理想码对比都跑过，
  `scenario_meta.csv` 里是实测结果（稳态 0 LSB）。
- ✅ **RTL 的定点运算也用逐位等价模型验证过**：`gen/golden_cic.py` 复刻了
  `amc1306_sample_stub.v` 的 32 位补码运算（含 CIC 积分器的有意环绕），
  对同一份 `scenario.mem` 解出的码与理想值**逐段 0 LSB**，说明"数据 + 期望码 + RTL 数据通路"
  三者自洽：如果 xsim 里你这个 DUT 跑出别的结果，问题在 DUT 或 TB 的时序/端口，不在激励。
- ⚠️ **RTL 与 testbench 未在本机编译/仿真过**：这台机器上没装 Vivado（`xvlog/xsim`
  不在 PATH，`C:\AMD` 下只有 Chipset_Software），所以 Verilog 部分是按 Vivado 2022.2 /
  xsim 的语言子集写的，但没有实际跑过一次编译。第一次运行时如果报语法错，大概率集中在
  两处：`$value$plusargs` 的字符串寄存器、`%0s` 打印定长字符串——这两处都只影响日志，
  改起来是单行。
- 数据手册引用见 `docs/spec_notes.md`，其中明确标注了哪几条是**原文**、哪几条是**反解**、
  哪几条**取不到**（Equation 1 是图片、"Test Pattern" 内容未公开）。
