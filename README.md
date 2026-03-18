# 二维码课堂签到系统（Flask）

## 功能
- 教师端生成动态签到二维码
- 学生扫码签到并填写 student ID
- student ID 必须在 `students.txt` 中才能签到
- 同一个 `student ID` 只能签到一次
- 同一台手机（浏览器设备标识）只能签到一次
- 教师端可查看签到结果并导出 CSV

## 目录
- `app.py`：主程序
- `students.txt`：合法 student ID 列表，每行一个
- `requirements.txt`：依赖

## 运行方法
```bash
pip install -r requirements.txt
python app.py
```

浏览器打开：
```bash
http://127.0.0.1:5000/admin
```

## 教师端默认 PIN
默认是：
```text
123456
```
建议你在 `app.py` 中修改：
```python
TEACHER_PIN = "123456"
```

## 使用流程
1. 进入教师端并登录
2. 上传 `students.txt`
3. 创建本节课签到二维码
4. 学生扫码，输入 student ID
5. 系统自动检查：
   - 学号是否在名单中
   - 学号是否已签到
   - 当前手机是否已签到过
6. 教师端查看签到结果并导出 CSV

## 重要说明
“一个手机只能签一次”只能提高代签成本，不能绝对杜绝代签，因为学生仍可能：
- 借手机给别人
- 清除浏览器缓存
- 换浏览器
- 使用无痕模式

如果你希望进一步加强防代签，建议后续加入：
- 校园网 / 教室 Wi-Fi 限制
- 地理位置校验
- 姓名 + 学号双重校验
- 动态口令
- 教师现场抽查
