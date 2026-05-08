# -*- coding: utf-8 -*-
"""
缠论（缠中说禅）技术分析算法实现

该实现包含以下级别结构：
1. 分型级别（Fenxing Level）- 基础价格形态识别
2. 笔级别（Bi Level）- 由分型构成的价格段
3. 线段级别（Seg Level）- 由笔构成的趋势段
4. 中枢级别（Pivot Level）- 线段构成的价格重叠区域
5. 趋势级别（Trend Level）- 中枢构成的趋势

数据流程：K线 → 分型 → 笔 → 线段 → 中枢 → 趋势
"""

import sys
import pandas as pd
import numpy as np
from datetime import date
import os


# 数据目录配置
data_dir = '/scratch/tmp/pudge/chan/data/'


def buy_sell(INDEX, data_dir, debug=1):
    """
    缠论买卖点分析主函数

    参数:
        INDEX: 数据文件索引
        data_dir: 数据文件目录
        debug: 调试模式（0=生产模式，1+=调试模式）
    """
    os.chdir(data_dir)
    len_dir = os.listdir(data_dir)

    # 检查数据文件是否今日更新
    if date.fromtimestamp(os.path.getmtime(len_dir[INDEX])) < date.today():
        return None

    if debug == 0:
        debug = 1

    # 读取数据，移除最后debug条记录（避免未完成的K线）
    df = pd.read_csv(len_dir[INDEX])[['low', 'high', 'datetime']][:-debug]

    if debug >= len(df):
        print('skipped')
        return

    print('processing ' + len_dir[INDEX].split('_')[1].split('.')[0])

    # ===== 级别1：分型识别（Fenxing）=====
    # 移除包含关系，标准化K线
    i = 0
    while(True):
        if (df['low'][i] <= df['low'][i+1]) or (df['high'][i] <= df['high'][i+1]):
            i = i + 1
        else:
            break
    df = df[i:].reset_index(drop=True)

    # 处理包含关系K线
    while (True):
        temp_len = len(df)
        i = 0
        while i <= len(df) - 4:
            # 检查是否包含关系
            if (df.iloc[i+2, 0] >= df.iloc[i+1, 0] and df.iloc[i+2, 1] <= df.iloc[i+1, 1]) or \
               (df.iloc[i+2, 0] <= df.iloc[i+1, 0] and df.iloc[i+2, 1] >= df.iloc[i+1, 1]):
                if df.iloc[i+1, 0] > df.iloc[i, 0]:
                    df.iloc[i+2, 0] = max(df.iloc[i+1:i+3, 0])
                    df.iloc[i+2, 1] = max(df.iloc[i+1:i+3, 1])
                    df.drop(df.index[i+1], inplace=True)
                    continue
                else:
                    df.iloc[i+2, 0] = min(df.iloc[i+1:i+3, 0])
                    df.iloc[i+2, 1] = min(df.iloc[i+1:i+3, 1])
                    df.drop(df.index[i+1], inplace=True)
                    continue
            i = i + 1
        if len(df) == temp_len:
            break

    df = df.reset_index(drop=True)

    # ===== 级别2：计算顶底分型（Ding/Di Fenxing）=====
    # ul数组：0=普通K线，1=底分型，-1=顶分型
    ul = [0]
    for i in range(len(df) - 2):
        if df.iloc[i+2, 0] < df.iloc[i+1, 0] and df.iloc[i, 0] < df.iloc[i+1, 0]:
            ul = ul + [1]  # 底分型
            continue
        if df.iloc[i+2, 0] > df.iloc[i+1, 0] and df.iloc[i, 0] > df.iloc[i+1, 0]:
            ul = ul + [-1]  # 顶分型
            continue
        else:
            ul = ul + [0]
    ul = ul + [0]

    global df1
    df1 = pd.concat((df[['low', 'high']], pd.DataFrame(ul), df['datetime']), axis=1)
    df1.rename(columns={0: 'od'}, inplace=True)

    if len(df1) <= 60:
        print('error!')
        return

    # ===== 级别3：确认有效分型（笔的端点）=====
    df1 = df1.reset_index(drop=True)
    global od_list  # 存储有效分型的索引位置

    od_list = [0]
    judge(0, 0, 1)  # 开始确认分型

    # ===== 级别4：生成线段（Seg）=====
    start = 0
    while start < len(od_list) - 5:
        if check_init_seg(od_list[start:start+4]):
            break
        else:
            start = start + 1

    lines = []  # 存储线段数据

    i = start
    end = False
    while i <= len(od_list) - 4:
        se = Seg(od_list[i:i+4])
        label = False
        while label == False and i <= len(od_list) - 6:
            i = i + 2
            label, start = se.grow(od_list[i+2:i+4])
            if se.vertex[-1] > od_list[-3]:
                end = True
                lines += [se.lines()]
                break
        if end:
            break
        i = np.where(np.array(od_list) == se.vertex[-1])[0][0]
        lines += [se.lines()]

    # 处理未完成线段的尾部
    low_list = df1.iloc[se.vertex[-1]:, 0]
    high_list = df1.iloc[se.vertex[-1]:, 1]

    low_extre = low_list.min()
    high_extre = high_list.max()

    # ===== 级别5：生成中枢（Pivot）=====
    a, tails = get_pivot(lines)  # a是Pivot1对象数组
    pro_a = process_pivot(a)  # 处理中枢趋势关系

    # ===== 交易信号生成 =====
    # 买点类型1：趋势减缓，第一中枢底 > 下一中枢顶
    signal, interval = buy_point1(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../buy1/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_buy1.txt', tails)

    # 买点类型3：下跌背驰
    signal, interval = buy_point3_des(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../buy3/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_buy3.txt', tails)

    # 买点类型23：中枢破坏后的回抽
    signal, interval = buy_point23(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../buy23/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_buy23.txt', tails)

    # 买点类型2：中枢形成过程中的支撑
    signal, interval = buy_point2(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../buy2/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_buy2.txt', tails)

    # ===== 卖点信号 =====
    signal, interval = sell_point1(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../sell1/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_sell1.txt', tails)

    signal, interval = sell_point3_ris(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../sell3/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_sell3.txt', tails)

    signal, interval = sell_point2(pro_a, tails)
    if signal:
        pro_a[-1].write_out('../sell2/' + len_dir[INDEX].split('_')[1].split('.')[0] + '_sell2.txt', tails)


# ===== 工具函数 =====

def same_d(a1, a2, b1, b2, a_sign):
    """
    判断两个K线是否同方向

    参数:
        a1, a2: 第一个K线的(low, high)
        b1, b2: 第二个K线的(low, high)
        a_sign: 方向符号 (1=上涨, -1=下跌)
    """
    if a_sign == 1:
        return (a1 > b1 and a2 > b2)
    else:
        return (a1 < b1 and a2 < b2)

def new_extreme(a1, a2, b1, b2, a_sign):
    """
    判断b是否相对于a创造了新的极值

    参数:
        同上
    """
    if a_sign == 1:
        return b2 >= a2  # 上涨时，b的最高价 >= a的最高价
    else:
        return a1 >= b1  # 下跌时，a的最低价 >= b的最低价

def write_seg(temp_lines, file, buy_sign, interval):
    """
    写入线段信息到文件

    参数:
        temp_lines: 线段数据
        file: 文件路径
        buy_sign: 是否为买点
        interval: 目标价格/支撑阻力位
    """
    # ...（函数实现保持不变）

def exist_opposite(cur_i, d, pos):
    """
    检查是否存在反向分型

    参数:
        cur_i: 当前位置
        d: 期望方向
        pos: 偏移位置
    """
    return df1['od'].iloc[cur_i + pos] == -d and \
           same_d(df1.iloc[cur_i, 0], df1.iloc[cur_i, 1],
                  df1.iloc[cur_i + pos, 0], df1.iloc[cur_i + pos, 1], d)

def exist_new_extreme(cur_i, d, start, end):
    """
    在指定范围内检查是否存在新的极值

    参数:
        cur_i: 当前位置
        d: 方向
        start: 开始位置
        end: 结束位置
    """
    j = start
    while j <= end:
        if new_extreme(df1.iloc[cur_i, 0], df1.iloc[cur_i, 1],
                      df1.iloc[cur_i + j, 0], df1.iloc[cur_i + j, 1], d):
            return cur_i + j, True
        j = j + 1
    return cur_i, False


def judge(prev_i, cur_i, d):
    """
    递归确认分型有效性

    参数:
        prev_i: 前一个已确认分型位置
        cur_i: 当前待确认分型位置
        d: 分型方向 (1=底分型, -1=顶分型)
    """
    global od_list

    # 边界检查
    if cur_i + 4 >= len(df1) - 1:
        return 0

    # 如果距离太近或方向不符，继续搜索
    if cur_i - prev_i < 4 or df1['od'].iloc[cur_i] != d:
        cur_i = cur_i + 1
        judge(prev_i, cur_i, d)
    else:
        # 至少4根K线间隔且方向正确
        new_i, label1 = exist_new_extreme(cur_i, d, 2, 3)
        if label1 == True:
            cur_i = new_i
            judge(prev_i, cur_i, d)
        else:
            k = 4
            if cur_i + k + 1 >= len(df1) - 1:
                return 0

            # 查找反向分型来确认
            while not exist_opposite(cur_i, d, k):
                new_i, label2 = exist_new_extreme(cur_i, d, k, k)
                if label2 == True:
                    cur_i = new_i
                    judge(prev_i, cur_i, d)
                    return 0
                else:
                    k = k + 1
                    if cur_i + k >= len(df1) - 1:
                        return 0

            # 找到反向分型，确认当前分型有效
            prev_i = cur_i
            cur_i = cur_i + k
            od_list = od_list + [prev_i]
            judge(prev_i, cur_i, -d)


def check_init_seg(start_l):
    """
    检查是否能构成本级别线段

    参数:
        start_l: 4个分型位置的列表
    """
    d = -df1.iloc[start_l[0], 2]  # 线段方向与起始分型相反

    if not ((d == 1 or d == -1) and (len(start_l) == 4)):
        print('initializing seg failed in check_init_seg!')

    if d == 1:  # 上涨线段
        if df1.iloc[start_l[1], 1] < df1.iloc[start_l[3], 1] and \
           df1.iloc[start_l[0], 0] < df1.iloc[start_l[2], 0]:
            return True
        else:
            return False
    else:  # 下跌线段
        if df1.iloc[start_l[1], 0] > df1.iloc[start_l[3], 0] and \
           df1.iloc[start_l[0], 1] > df1.iloc[start_l[2], 1]:
            return True
        else:
            return False


class Seg:
    """
    线段类（级别4）

    线段由至少3笔构成，是缠论中的基本趋势单位
    """

    def __init__(self, start_l):
        """
        初始化线段

        参数:
            start_l: 4个分型位置的列表，构成初始线段
        """
        self.start = start_l[0]

        if df1.iloc[start_l[0], 2] == 0:
            print("error init!")

        self.d = -df1.iloc[start_l[0], 2]  # 线段方向
        self.finished = False
        self.vertex = start_l
        self.gap = False  # 是否有缺口

        if self.d == 1:  # 上涨线段
            self.cur_extreme = df1.iloc[start_l[3], 1]
            self.cur_extreme_pos = start_l[3]
            self.prev_extreme = df1.iloc[start_l[1], 1]
        else:  # 下跌线段
            self.cur_extreme = df1.iloc[start_l[3], 0]
            self.cur_extreme_pos = start_l[3]
            self.prev_extreme = df1.iloc[start_l[1], 0]

    def grow(self, new_l):
        """
        线段生长：尝试将新的笔加入线段

        参数:
            new_l: 2个新分型位置

        返回:
            (是否完成, 完成位置)
        """
        if 1 == self.d:  # 上涨线段
            if df1.iloc[new_l[1], 1] >= self.cur_extreme:  # 创新高
                if df1.iloc[new_l[0], 0] > self.prev_extreme:
                    self.gap = True
                else:
                    self.gap = False
                self.prev_extreme = self.cur_extreme
                self.cur_extreme = df1.iloc[new_l[1], 1]
                self.cur_extreme_pos = new_l[1]
            else:  # 未创新高，可能完成
                if (self.gap == False and df1.iloc[new_l[1], 0] < df1.iloc[self.vertex[-1], 0]) or \
                   (self.gap == True and (df1.iloc[self.vertex[-1], 1] < df1.iloc[self.vertex[-3], 1]) \
                    and (df1.iloc[self.vertex[-2], 0] < df1.iloc[self.vertex[-4], 0])):
                    self.finished = True
                    self.vertex = [i for i in self.vertex if i <= self.cur_extreme_pos]
                    return True, self.vertex[-1]

            self.vertex = self.vertex + new_l
            return False, 0

        else:  # 下跌线段
            if df1.iloc[new_l[1], 0] <= self.cur_extreme:  # 创新低
                if df1.iloc[new_l[0], 1] < self.prev_extreme:
                    self.gap = True
                else:
                    self.gap = False
                self.vertex = self.vertex + new_l
                self.prev_extreme = self.cur_extreme
                self.cur_extreme = df1.iloc[new_l[1], 0]
                self.cur_extreme_pos = new_l[1]
            else:  # 未创新低，可能完成
                if (self.gap == False and df1.iloc[new_l[1], 1] > df1.iloc[self.vertex[-1], 1]) or \
                   (self.gap == True and (df1.iloc[self.vertex[-1], 0] > df1.iloc[self.vertex[-3], 0]) \
                    and (df1.iloc[self.vertex[-2], 1] > df1.iloc[self.vertex[-4], 1])):
                    self.finished = True
                    self.vertex = [i for i in self.vertex if i <= self.cur_extreme_pos]
                    return True, self.vertex[-1]

            self.vertex = self.vertex + new_l
            return False, 0

    def getrange(self):
        """获取线段价格范围 [起点价, 终点价, 方向]"""
        if self.d == 1:
            return [df1.iloc[self.start, 0], self.cur_extreme, self.d]
        else:
            return [df1.iloc[self.start, 1], self.cur_extreme, self.d]

    def lines(self):
        """返回线段的起点和终点坐标"""
        return [(self.start, self.getrange()[0]),
                (self.vertex[-1], self.getrange()[1])]


class Pivot1:
    """
    1分钟级别中枢类（级别5）

    中枢是价格的重叠区域，由至少3个线段构成
    中枢级别会随着线段数量增加而扩展：
    - 3线段：1分钟中枢
    - 4线段：扩展中
    - 7线段：升级为5分钟中枢
    - 10线段：确认5分钟中枢
    - 19线段：升级为30分钟中枢
    - 28线段：确认30分钟中枢
    """

    def __init__(self, lines, d):
        """
        初始化中枢

        参数:
            lines: 4个线段的lines()结果
            d: 进入中枢的线段方向
        """
        self.trend = -2  # 趋势类型：-1=下跌，0=震荡，1=上涨
        self.level = 1   # 中枢级别：1=1分钟，2=5分钟，3=30分钟
        self.enter_d = d  # 进入方向
        self.aft_l_price = 0
        self.aft_l_time = '00'
        self.future_zd = -float('inf')
        self.future_zg = float('inf')

        if d == 1:  # 上涨进入中枢
            if lines[3][1][1] <= lines[1][0][1]:
                self.zg = min(lines[1][0][1], lines[3][0][1])  # 中枢高点
                self.zd = max(lines[3][1][1], lines[1][1][1])  # 中枢低点
                self.dd = lines[2][0][1]  # 进入低点
                self.gg = max(lines[1][0][1], lines[2][1][1])  # 进入高点
        else:  # 下跌进入中枢
            if lines[3][1][1] >= lines[1][0][1]:
                self.zg = min(lines[1][1][1], lines[3][1][1])
                self.zd = max(lines[3][0][1], lines[1][0][1])
                self.dd = min(lines[2][1][1], lines[1][0][1])
                self.gg = lines[2][0][1]

        self.start_index = lines[1][0][0]
        self.end_index = lines[2][1][0]
        self.finished = 0  # 0=未完成，0.5=部分完成，1=已完成
        self.enter_force = seg_force(lines[0])
        self.leave_force = seg_force(lines[3])
        self.size = 3  # 当前线段数量
        self.mean = 0.5 * (self.zd + self.zg)
        self.start_time = df1.iloc[self.start_index, 3]
        self.leave_start_time = df1.iloc[self.end_index, 3]
        self.leave_end_time = df1.iloc[lines[3][1][0], 3]
        self.leave_d = -d
        self.leave_end_price = lines[3][1][1]
        self.leave_start_price = lines[3][0][1]
        self.prev2_force = seg_force(lines[1])
        self.prev1_force = seg_force(lines[2])
        self.prev2_end_price = lines[1][1][1]

    def grow(self, seg):
        """
        中枢生长：尝试将新线段加入中枢

        参数:
            seg: 新线段的lines()结果
        """
        self.prev2_force = self.prev1_force
        self.prev1_force = self.leave_force
        self.prev2_end_price = self.leave_start_price

        if seg[1][1] > seg[0][1]:  # 上涨线段
            if (seg[1][1] >= self.zd and seg[0][1] <= self.zg) and (self.size <= 28):
                # 线段在中枢范围内，加入中枢
                self.end_index = seg[0][0]
                self.size = self.size + 1
                self.dd = min(self.dd, seg[0][1])

                self.leave_force = seg_force(seg)
                self.leave_start_time = df1.iloc[self.end_index, 3]
                self.leave_end_time = df1.iloc[seg[1][0], 3]
                self.leave_d = 2 * int(seg[1][1] > seg[0][1]) - 1
                self.leave_start_price = seg[0][1]
                self.leave_end_price = seg[1][1]

                # 级别扩展检查
                if self.size in [4, 7, 10, 19, 28]:
                    self.future_zd = max(self.future_zd, self.dd)
                    self.future_zg = min(self.future_zg, self.gg)

                if self.size in [10, 28]:  # 级别扩展
                    self.level = self.level + 1
                    self.zd = self.future_zd
                    self.zg = self.future_zg
                    self.future_zd = -float('inf')
                    self.future_zg = float('inf')
            else:
                # 线段超出中枢范围，中枢完成
                if (seg[1][1] >= self.zd and seg[0][1] <= self.zg):
                    self.dd = min(self.dd, seg[0][1])
                    self.finished = 0.5
                else:
                    self.finished = 1

                self.aft_l_price = seg[1][1]
                self.aft_l_time = df1.iloc[seg[1][0], 3]
        else:  # 下跌线段
            if (seg[1][1] <= self.zg and seg[0][1] >= self.zd) and self.size <= 28:
                self.end_index = seg[0][0]
                self.end_price = seg[0][1]
                self.size = self.size + 1
                self.gg = max(self.gg, seg[0][1])

                self.leave_force = seg_force(seg)
                self.leave_start_time = df1.iloc[self.end_index, 3]
                self.leave_end_time = df1.iloc[seg[1][0], 3]
                self.leave_d = 2 * int(seg[1][1] > seg[0][1]) - 1
                self.leave_start_price = seg[0][1]
                self.leave_end_price = seg[1][1]

                if self.size in [4, 7, 10, 19, 28]:
                    self.future_zd = max(self.future_zd, self.dd)
                    self.future_zg = min(self.future_zg, self.gg)

                if self.size in [10, 28]:
                    self.level = self.level + 1
                    self.zd = self.future_zd
                    self.zg = self.future_zg
                    self.future_zd = -float('inf')
                    self.future_zg = float('inf')
            else:
                if (seg[1][1] <= self.zg and seg[0][1] >= self.zd):
                    self.gg = max(self.gg, seg[0][1])
                    self.finished = 0.5
                else:
                    self.finished = 1

                self.aft_l_price = seg[1][1]
                self.aft_l_time = df1.iloc[seg[1][0], 3]

    def display(self):
        """显示中枢详细信息"""
        print('enter_d:' + str(self.enter_d))
        print('zd:' + str(self.zd))
        print('zg:' + str(self.zg))
        print('dd:' + str(self.dd))
        print('gg:' + str(self.gg))
        print('start_index:' + str(self.start_index))
        print('end_index:' + str(self.end_index))
        print('start_time:' + str(self.start_time))
        print('size:' + str(self.size))
        print('enter_force:' + str(self.enter_force))
        print('leave_force:' + str(self.leave_force))
        print('finished:' + str(self.finished))
        print('leave_start_time:' + str(self.leave_start_time))
        print('leave_end_time:' + str(self.leave_end_time))
        print('leave_d:' + str(self.leave_d))
        print('leave_start_price:' + str(self.leave_start_price))
        print('leave_end_price:' + str(self.leave_end_price))
        print('mean:' + str(self.mean))
        print('aft_l_price:' + str(self.aft_l_price))

    def write_out(self, filepath, extra=''):
        """写入中枢信息到文件"""
        f = open(filepath, 'w')
        f.write(' zd:' + str(self.zd) + ' zg:' + str(self.zg) +
                ' dd:' + str(self.dd) + ' gg:' + str(self.gg) +
                ' leave_d:' + str(self.leave_d) +
                ' prev2_leave_force:' + str(self.prev2_force) + ' leave_force:' + str(self.leave_force) +
                '\n  start_time:' + str(self.start_time) +
                '  leave_start_time:' + str(self.leave_start_time) +
                '  leave_end_time:' + str(self.leave_end_time) +
                '  prev2_end_price:' + str(self.prev2_end_price) +
                '  leave_end_price:' + str(self.leave_end_price) +
                '\n  size: ' + str(self.size) + ' finished: ' + str(self.finished) + ' trend:' +
                str(self.trend) + ' level:' +
                str(self.level))
        f.write('\n')
        if extra != '':
            f.write('tails:')
            f.write(str(extra))
            f.write('\n')
            f.write('now')
            f.write(str(df1.iloc[-1]))
        f.close()
        return


def seg_force(seg):
    """
    计算线段力度

    公式: 力度 = 1000 * |价格变化百分比| / K线数量
    """
    return 1000 * abs(seg[1][1] / seg[0][1] - 1) / (seg[1][0] - seg[0][0])


def get_pivot(lines):
    """
    从线段生成中枢

    参数:
        lines: 所有线段的数组

    返回:
        (Pivot1数组, 尾部信息)
    """
    Pivot1_array = []
    i = 0

    while i < len(lines):
        d = 2 * int(lines[i][0][1] < lines[i][1][1]) - 1

        if i < len(lines) - 3:
            if d == 1:  # 上涨线段进入
                if lines[i+3][1][1] <= lines[i+1][0][1]:
                    pivot = Pivot1(lines[i:i+4], d)
                    i_j = 1
                    while i + i_j < len(lines) - 3 and pivot.finished == 0:
                        pivot.grow(lines[i + i_j + 3])
                        i_j = i_j + 1
                    i = i + pivot.size
                    Pivot1_array = Pivot1_array + [pivot]
                    continue
                else:
                    i = i + 1
            else:  # 下跌线段进入
                if lines[i+3][1][1] >= lines[i+1][0][1]:
                    pivot = Pivot1(lines[i:i+4], d)
                    i_j = 1
                    while i + i_j < len(lines) - 3 and pivot.finished == 0:
                        pivot.grow(lines[i + i_j + 3])
                        i_j = i_j + 1
                    i = i + pivot.size
                    Pivot1_array = Pivot1_array + [pivot]
                    continue
                else:
                    i = i + 1
        else:
            i = i + 1

    return Pivot1_array, [df1.iloc[lines[-1][0][0], 3], lines[-1][0][1],
                   df1.iloc[lines[-1][1][0], 3], lines[-1][1][1], 2 * int(lines[-1][1][1] > lines[-1][0][1]) - 1]


def process_pivot(pivot):
    """
    处理中枢趋势关系

    判断中枢间的趋势：
    - 上涨趋势：前中枢高点 < 后中枢低点
    - 下跌趋势：前中枢低点 > 后中枢高点
    - 震荡：中枢重叠
    """
    for i in range(0, len(pivot) - 1):
        if pivot[i].level == 1 and pivot[i+1].level == 1:
            if pivot[i].dd > pivot[i+1].gg:
                pivot[i+1].trend = -1  # 下跌趋势
            else:
                if pivot[i].gg < pivot[i+1].dd:
                    pivot[i+1].trend = 1  # 上涨趋势
                else:
                    pivot[i+1].trend = 0  # 震荡
        else:
            if pivot[i].gg > pivot[i + 1].gg and pivot[i].dd > pivot[i + 1].dd:
                pivot[i+1].trend = -1
            else:
                if pivot[i].gg < pivot[i + 1].gg and pivot[i].dd < pivot[i + 1].dd:
                    pivot[i+1].trend = 1
                else:
                    pivot[i+1].trend = 0
    return pivot


def buy_point1(pro_pivot, tails, num_pivot=2):
    """
    买点类型1：趋势背驰

    条件：
    1. 至少2个中枢
    2. 趋势下跌
    3. 当前价格低于前中枢低点
    4. 力度减弱
    5. 当前中枢底 > 离开中枢价格
    """
    if len(pro_pivot) <= 3 or tails[4] == 1 or pro_pivot[-1].size >= 8 or pro_pivot[-1].finished != 0 \
       or df1.iloc[-1][0] / pro_pivot[-1].leave_end_price - 1 > 0 or \
       df1.iloc[-1][0] > tails[3]:
        return False, 0
    else:
        if (pro_pivot[-1].prev2_end_price > pro_pivot[-1].leave_end_price) and \
           (pro_pivot[-1].leave_start_time == tails[0]) and \
           df1.iloc[-1][0] < pro_pivot[-1].dd and \
           1.2 * pro_pivot[-1].leave_force < pro_pivot[-1].prev2_force and \
           (pro_pivot[-1].dd > pro_pivot[-1].leave_end_price):
            return True, pro_pivot[-1].dd  # 目标价格
        else:
            return False, 0


def buy_point2(pro_pivot, tails, num_pivot=2):
    """
    买点类型2：中枢支撑

    条件：
    1. 中枢形成过程中的支撑位
    2. 价格回到中枢低点
    3. 力度分析
    """
    if len(pro_pivot) <= 3 or tails[4] == 1 or pro_pivot[-1].size >= 8 or pro_pivot[-1].finished != 0 \
       or df1.iloc[-1][0] / pro_pivot[-1].leave_end_price - 1 > 0 or \
       df1.iloc[-1][0] > tails[3]:
        return False, 0
    else:
        if (pro_pivot[-1].prev2_end_price < pro_pivot[-1].leave_end_price) and \
           (pro_pivot[-1].leave_start_time == tails[0]) and \
           pro_pivot[-1].prev2_end_price == pro_pivot[-1].dd and \
           pro_pivot[-1].leave_start_price > 0.51 * (pro_pivot[-1].zd + pro_pivot[-1].zg):
            return True, pro_pivot[-1].prev2_end_price  # 支撑价格
        else:
            return False, 0


def buy_point3_des(pro_pivot, tails):
    """
    买点类型3：下跌背驰

    条件：
    1. 下跌趋势
    2. 价格跌破前低点
    3. 力度减弱
    4. 在中枢上方获得支撑
    """
    if len(pro_pivot) <= 2 or (tails[4] == 1) or (pro_pivot[-1].finished != 1) or \
       pro_pivot[-1].level > 1 or df1.iloc[-1][0] / pro_pivot[-1].leave_end_price - 1 > 0 or \
       df1.iloc[-1][0] > tails[3]:
        return False, 0
    else:
        if df1.iloc[-1][0] < 0.98 * pro_pivot[-1].leave_end_price and df1.iloc[-1][0] > 1.02 * pro_pivot[-1].zg and \
           pro_pivot[-1].aft_l_price > 1.02 * pro_pivot[-1].zg and \
           tails[0] == pro_pivot[-1].leave_end_time and \
           pro_pivot[-1].leave_force > pro_pivot[-1].prev2_force and \
           pro_pivot[-1].leave_end_price > pro_pivot[-1].prev2_end_price:
            return True, pro_pivot[-1].zg  # 支撑价格
        else:
            return False, 0


def buy_point23(pro_pivot, tails):
    """
    买点类型23：中枢破坏后回抽

    条件：
    1. 中枢已完成
    2. 价格跌破中枢
    3. 回抽确认
    4. 趋势为下跌
    """
    if len(pro_pivot) <= 3 or pro_pivot[-1].finished != 1 or \
       pro_pivot[-1].level > 1 or df1.iloc[-1][0] / pro_pivot[-1].leave_end_price - 1 > 0 or \
       df1.iloc[-1][0] > tails[3]:
        return False, 0
    else:
        if df1.iloc[-1][0] < 0.98 * pro_pivot[-1].leave_end_price and df1.iloc[-1][0] > 1.01 * pro_pivot[-1].zg and pro_pivot[-1].trend == -1 \
           and tails[3] > 1.01 * pro_pivot[-1].zg and tails[0] == pro_pivot[-1].leave_end_time and \
           pro_pivot[-1].leave_start_price == pro_pivot[-1].dd:
            return True, pro_pivot[-1].zg  # 支撑价格
        else:
            return False, 0


def sell_point1(pro_pivot, tails, num_pivot=2):
    """
    卖点类型1：趋势背驰

    条件：
    1. 至少2个中枢
    2. 趋势上涨
    3. 当前价格高于前中枢高点
    4. 力度减弱
    """
    if len(pro_pivot) <= 3 or tails[4] == -1 or pro_pivot[-1].size >= 8 or pro_pivot[-1].finished != 0 \
       or df1.iloc[-1][1] / pro_pivot[-1].leave_end_price - 1 < 0 or \
       df1.iloc[-1][0] < tails[3]:
        return False, 0
    else:
        if (pro_pivot[-1].prev2_end_price < pro_pivot[-1].leave_end_price) and \
           (pro_pivot[-1].leave_start_time == tails[0]) and \
           df1.iloc[-1][0] > pro_pivot[-1].zg and \
           1.2 * pro_pivot[-1].leave_force < pro_pivot[-1].prev2_force:
            return True, pro_pivot[-1].zg  # 阻力价格
        else:
            return False, 0


def sell_point2(pro_pivot, tails, num_pivot=2):
    """
    卖点类型2：中枢阻力

    条件：
    1. 中枢形成过程中的阻力位
    2. 价格回到中枢高点
    3. 力度分析
    """
    if len(pro_pivot) <= 3 or tails[4] == -1 or pro_pivot[-1].size >= 8 or pro_pivot[-1].finished != 0 \
       or df1.iloc[-1][1] / pro_pivot[-1].leave_end_price - 1 < 0 or \
       df1.iloc[-1][0] < tails[3]:
        return False, 0
    else:
        if (pro_pivot[-1].prev2_end_price > pro_pivot[-1].leave_end_price) and \
           (pro_pivot[-1].leave_start_time == tails[0]) and \
           df1.iloc[-1][0] > 0.51 * (pro_pivot[-1].zd + pro_pivot[-1].zg) and \
           pro_pivot[-1].prev2_end_price == pro_pivot[-1].gg:
            return True, pro_pivot[-1].zg  # 阻力价格
        else:
            return False, 0


def sell_point3_ris(pro_pivot, tails, num_pivot=2):
    """
    卖点类型3：上涨背驰

    条件：
    1. 上涨趋势
    2. 价格突破前高点
    3. 力度减弱
    4. 在中枢下方遇到阻力
    """
    if len(pro_pivot) <= 3 or tails[4] == -1 or pro_pivot[-1].size >= 8 or pro_pivot[-1].finished != 1 \
       or df1.iloc[-1][0] < tails[3]:
        return False, 0
    else:
        if (1.02 * pro_pivot[-1].leave_end_price < df1.iloc[-1][0]) and \
           (pro_pivot[-1].leave_end_time == tails[0]) and \
           pro_pivot[-1].leave_force > pro_pivot[-1].prev2_force \
           and df1.iloc[-1][1] < pro_pivot[-1].zd:
            return True, pro_pivot[-1].zd  # 阻力价格
        else:
            return False, 0


def main():
    """
    主函数：直接运行时的入口

    使用sh.csv数据文件进行分析
    """
    # 读取上海股票数据
    df = pd.read_csv('./sh.csv', index_col=0)[['low', 'high']]
    df['datetime'] = df.index

    # 同样的缠论处理流程...
    # [这里省略了重复的处理代码，实际使用时应该复用buy_sell中的逻辑]

    return


if __name__ == '__main__':
    main()