# 泰国 TikTok Shop AI选品系统 项目进度记录

日期：
2026-09-15

## 已完成模块

### 1. 官方类目体系
来源：
C:\Users\Administrator\Desktop\泰国TK官方类目.txt

结果：
- 一级类目 L1：30个
- 二级类目 L2：219个

已建立：
- app\category_loader.py
- scripts\collect\build_collection_tasks.py
- scripts\collect\build_final_collection_tasks.py


### 2. TikTok页面映射体系

文件：
data\category_mapping\tiktok_th_category_page_mapping.json

验证结果：
- L1：30/30成功
- L2：
  - MATCH：197
  - ALIAS：18
  - FAILED：4

规则：
- 官方名称用于数据保存
- 页面名称用于TikTok点击


### 3. Windows独立采集程序

入口：

scripts\collect\run_all_collection.py

功能：
- Windows本地执行
- 不依赖Coze
- 使用Chrome Profile登录态
- 自动采集TikTok后台
- 支持断点恢复
- 单任务失败不中断


### 4. 数据保存体系

原始数据：

data\_batches\<batch_id>\

结构：

L1
 └── L2
      └── monthly
           └── TXT


数据库：

data\tiktok_market.db


表：
- category
- keyword_metric
- collection_batch


### 5. 家居用品测试采集

批次：

data\_batches\20260915_214114


一级类目：

家居用品


7个L2：

- 家居收纳用品
- 卫浴用品
- 装饰
- 家庭护理用品
- 洗衣工具
- 节庆和派对用品
- 家居日用


结果：

7成功 / 0失败


周期：

2026-08-01 至 2026-08-31


### 6. 数据标准化入库

执行：

python scripts\collect\normalize_top20_data.py --txt-root data\_batches\20260915_214114 --db data\tiktok_market.db


结果：

文件：
7

成功：
7

失败：
0

入库：
828行


说明：
理论840行，由于卫浴用品实际18条，所以减少12条。


## 当前阶段

已经完成：

✅ 类目体系
✅ 页面映射
✅ Windows采集程序
✅ 数据保存结构
✅ TXT标准化
✅ 数据库入库
✅ 查询验证
✅ 家居用品一级类目测试


## 下一阶段

执行：

215个有效L2正式采集

流程：

TikTok后台采集
↓
TXT保存
↓
normalize标准化
↓
写入tiktok_market.db


注意：

不要修改：
- V1筛选逻辑
- AI分析模块
- 1688模块
- 前端模块

保持当前架构继续开发。

