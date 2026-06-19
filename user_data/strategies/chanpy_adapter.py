"""
chan.py 适配器 — 封装 chan.py 库，输出缠论买卖点信号
替代原来的 chanlun_adapter.py，使用完整的 chan.py 管线
"""
import sys
from pathlib import Path

# 确保 chan_lib 在路径中（策略在 user_data/strategies/，所以上溯3级到项目根）
_chan_root = Path(__file__).resolve().parent.parent.parent / "chan_lib"
if not _chan_root.exists():
    _chan_root = Path("/root/freqtrade_bot/chan_lib")
if str(_chan_root) not in sys.path:
    sys.path.insert(0, str(_chan_root))

from Chan import CChan
from ChanConfig import CChanConfig
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE, DATA_FIELD
from Common.CTime import CTime
from KLine.KLine_Unit import CKLine_Unit


class ChanPySignals:
    """chan.py 信号生成器"""

    def __init__(self, code: str = "default"):
        self.code = code

    def analyze(self, dataframe, pair: str = "default") -> dict:
        """
        对单个币种的 DataFrame 运行缠论分析

        返回 dict: {
            'buy_times': set of (y,m,d,h,min),
            'sell_times': set of (y,m,d,h,min),
        }
        """
        klus = self._df_to_klus(dataframe)
        if len(klus) < 100:
            return {'buy_times': set(), 'sell_times': set()}

        bsp_list = self._run_chan(klus)
        return self._extract_signals(bsp_list)

    def _df_to_klus(self, dataframe):
        """DataFrame → CKLine_Unit 列表"""
        klus = []
        for idx, row in dataframe.iterrows():
            dt = row['date'].to_pydatetime()
            item = {
                DATA_FIELD.FIELD_TIME: CTime(dt.year, dt.month, dt.day,
                                             dt.hour, dt.minute, auto=False),
                DATA_FIELD.FIELD_OPEN: float(row['open']),
                DATA_FIELD.FIELD_HIGH: float(row['high']),
                DATA_FIELD.FIELD_LOW: float(row['low']),
                DATA_FIELD.FIELD_CLOSE: float(row['close']),
                DATA_FIELD.FIELD_VOLUME: float(row['volume']),
            }
            klus.append(CKLine_Unit(item, autofix=True))
        return klus

    def _run_chan(self, klus):
        """运行 chan.py 缠论分析，返回买卖点列表"""
        config = CChanConfig({
            "bi_strict": True,
            "trigger_step": True,
            "divergence_rate": 0.7,
            "bsp2_follow_1": False,
            "bsp3_follow_1": False,
            "min_zs_cnt": 1,
            "bs1_peak": True,
            "macd_algo": "peak",
            "bs_type": "1,2,3a,1p,2s,3b",
            "print_warning": False,
            "zs_algo": "normal",
            "kl_data_check": False,
            "max_kl_misalgin_cnt": 999999,
            "max_kl_inconsistent_cnt": 999999,
        })

        chan = CChan(
            code=self.code,
            lv_list=[KL_TYPE.K_3M],  # 3分钟K线
            config=config,
            data_src=DATA_SRC.CCXT,
            autype=AUTYPE.QFQ,
        )

        try:
            chan.trigger_load({KL_TYPE.K_3M: klus})
        except Exception as e:
            print(f"[ChanPy] trigger_load error for {self.code}: {e}")
            return []

        return chan.get_latest_bsp(number=0)

    def _extract_signals(self, bsp_list):
        """提取买卖点时间"""
        buy_times = set()
        sell_times = set()
        for bsp in bsp_list:
            t = bsp.klu.time
            tk = (t.year, t.month, t.day, t.hour, t.minute)
            if bsp.is_buy:
                buy_times.add(tk)
            else:
                sell_times.add(tk)
        return {'buy_times': buy_times, 'sell_times': sell_times}
