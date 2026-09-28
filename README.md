# ETF T+0 纸面台

A 股场内 T+0 ETF 的修订方案和纸面交易台。行情用 eltdx 的通达信 5 分钟线与五档，AxData 提供归一化 ETF 快照；IOPV 从 AxData 同一条腾讯快照报文里读取。程序只做纸面成交，不发送券商委托。

规则和样本成绩见 [docs/strategy-v2.md](docs/strategy-v2.md)。按步骤在本机启动见 [docs/运行.md](docs/运行.md)。开盘后如何对照页面下单见 [docs/操作.md](docs/操作.md)。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

浏览器打开 `http://127.0.0.1:8080`。Windows 的虚拟环境和启动命令在运行文档里。
