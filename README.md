# ETF T+0 纸面台

A 股场内 T+0 ETF 的修订方案和纸面交易台。行情用 eltdx 的通达信 5 分钟线与五档，AxData 提供归一化 ETF 快照；IOPV 从 AxData 同一条腾讯快照报文里读取。程序只做纸面成交，不发送券商委托。

规则、样本区间和扣费后的成绩见 [docs/strategy-v2.md](docs/strategy-v2.md)。

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
```

打开 `http://127.0.0.1:8080`。首次启动会向通达信主站拉取十只 ETF 的 5 分钟线，大约需要二十秒。

```bash
python -m pytest tests/test_strategy.py
```
