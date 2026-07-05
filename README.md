# Smart (Con)lock tuya

[![HACS](https://img.shields.io/badge/HACS-Custom-orange?style=flat-square)](https://hacs.xyz/)
[![GitHub Release](https://img.shields.io/github/v/release/batty211/smartlock-tuya?style=flat-square)](https://github.com/batty211/smartlock-tuya/releases)
[![License](https://img.shields.io/github/license/batty211/smartlock-tuya?style=flat-square)](LICENSE)

Integration สำหรับควบคุม Smart Lock ของ Tuya ผ่าน Home Assistant โดยใช้ Tuya Cloud API

เหมาะกับคนที่ใช้ Tuya / Smart Life แล้ว Home Assistant เห็นอุปกรณ์ล็อกเป็นแค่ sensor หรือกดปลดล็อกจาก Home Assistant ไม่ได้ Integration นี้เพิ่ม entity สำหรับสั่งปลดล็อก, ดูแบตเตอรี่, ดูสถานะออนไลน์ และรองรับล็อกวิดีโอประเภท `jtmspro` เช่น Conlock / video smart lock

## สิ่งที่จะได้ใน Home Assistant

| Entity                                    | ใช้ทำอะไร                                                                    |
| ----------------------------------------- | ---------------------------------------------------------------------------- |
| `lock.<ชื่ออุปกรณ์>`                      | ปุ่มสั่งล็อก/ปลดล็อก สำหรับอุปกรณ์ทั่วไป หรือปุ่มสั่งปลดล็อกสำหรับ `jtmspro` |
| `sensor.<ชื่ออุปกรณ์>_battery`            | สถานะแบตเตอรี่จาก Tuya                                                       |
| `binary_sensor.<ชื่ออุปกรณ์>_online`      | สถานะออนไลน์ของอุปกรณ์ `jtmspro`                                             |
| `binary_sensor.<ชื่ออุปกรณ์>_call_active` | แสดงว่าตอนนี้มีคำขอ/กริ่ง/วิดีโอคอลที่อนุญาตให้กดปลดล็อกหรือไม่              |
| `lock.<ชื่ออุปกรณ์>_physical_status`      | สถานะล็อกจริงของประตูสำหรับ `jtmspro` ใช้ดูหรือปรับเองใน Home Assistant      |
| `image.<ชื่ออุปกรณ์>_latest_image`        | รูปล่าสุดจากกริ่ง/วิดีโอคอล ถ้า Tuya ส่ง URL รูปที่ใช้งานได้                 |

## การใช้งานกับล็อก `jtmspro`

สำหรับล็อกวิดีโออย่าง `jtmspro` ตัวล็อกมักปลดล็อกผ่าน Cloud ได้ แต่สั่งล็อกกลับไม่ได้ และบางรุ่นไม่ส่งสถานะจริงว่าในตอนนี้ประตูล็อกอยู่หรือปลดล็อกอยู่ Integration จึงแยกเป็น 2 entity:

### 1. `lock.<ชื่ออุปกรณ์>`

ตัวนี้คือปุ่มสั่งปลดล็อกเท่านั้น

- ปุ่มนี้จะแสดง action เป็น **Unlock** เสมอ
- ถ้าไม่มีคนกดกริ่ง/ไม่มี video call ล่าสุด ปุ่มจะถูกปิดไว้เพื่อกันการกดปลดล็อกมั่ว
- ถ้าอุปกรณ์ออนไลน์และมี `Call Active` อยู่ ปุ่มจะกดได้
- `Call Active` เปิดจากเหตุการณ์ที่ Tuya ส่งมาให้เท่านั้น และจะปิดเองหลังประมาณ 60 วินาที
- เมื่อกดสำเร็จ Integration จะส่งคำสั่งปลดล็อกไปที่ Tuya
- ปุ่มนี้ไม่ใช่สถานะล็อกจริงของประตู

### 2. `lock.<ชื่ออุปกรณ์>_physical_status`

ตัวนี้คือสถานะล็อกจริงที่ใช้ดูใน Dashboard หรือใช้กับ automation

- กด `Lock` หรือ `Unlock` เองได้เพื่อบอก Home Assistant ว่าประตูล็อกอยู่หรือปลดล็อกอยู่
- หลังจากสั่งปลดล็อกผ่านปุ่มหลักสำเร็จ สถานะนี้จะเปลี่ยนเป็น `unlocked`
- ถ้าตัวล็อกส่งสถานะจริงกลับมาผ่าน Tuya ในอนาคต Integration จะใช้ค่านั้นแทน
- ถ้าคุณกดแก้สถานะเอง การแก้ล่าสุดของคุณจะไม่ถูก event เก่าเขียนทับ

สรุปสั้น ๆ:

- อยากเปิดประตู: กด `lock.<ชื่ออุปกรณ์>`
- อยากดู/บอกสถานะจริงของประตู: ใช้ `lock.<ชื่ออุปกรณ์>_physical_status`
- อยากรู้ว่าปลดล็อกได้ตอนนี้ไหม: ดู `binary_sensor.<ชื่ออุปกรณ์>_call_active`

## สิ่งที่ต้องเตรียม

ก่อนติดตั้งต้องมี Tuya IoT Project และผูกบัญชี Tuya / Smart Life เข้ากับโปรเจกต์ให้เรียบร้อย

### 1. สร้าง Tuya IoT Project

1. เข้า [iot.tuya.com](https://iot.tuya.com)
2. ไปที่ **Cloud** > **Development** > **Create Cloud Project**
3. เลือก Data Center ให้ตรงกับบัญชี Tuya / Smart Life ของคุณ
4. เลือก Development Method เป็น **Smart Home**
5. สร้างโปรเจกต์ให้เสร็จ

### 2. ผูกบัญชี Tuya / Smart Life

1. ในโปรเจกต์ Tuya IoT ไปที่ **Devices** > **Link Tuya App Account**
2. กดเพิ่มบัญชี
3. เปิดแอป Tuya หรือ Smart Life ในมือถือ
4. ใช้แอปสแกน QR code จากหน้า Tuya IoT
5. ยืนยันการผูกบัญชี
6. ตรวจสอบว่าอุปกรณ์ล็อกโผล่ในหน้า Devices แล้ว

### 3. เปิดบริการ API ที่จำเป็น

ในหน้า **Service API** ของ Tuya IoT Project ให้เปิดบริการเหล่านี้:

- **IoT Core**
- **Smart Lock Open Service**
- **Device Status Notification** สำหรับล็อก `jtmspro`

ถ้าต้องการดูรูปหรือสื่อจากล็อกวิดีโอ อาจต้องเปิดบริการวิดีโอ/สื่อของ Tuya เพิ่ม ขึ้นอยู่กับรุ่นและบัญชี Tuya ของคุณ

### 4. เปิด Remote Unlock ในแอป Tuya / Smart Life

1. เปิดแอป Tuya หรือ Smart Life
2. เข้าอุปกรณ์ล็อกของคุณ
3. ไปที่ Settings ของอุปกรณ์
4. เปิด **Remote Unlock** หรือ **Remote Unlock Without Password**

### 5. เตรียม Access ID และ Access Secret

ใน Tuya IoT Project ไปที่หน้า Overview แล้วคัดลอก:

- **Access ID**
- **Access Secret**

ต้องใช้ตอนเพิ่ม Integration ใน Home Assistant

## วิธีติดตั้ง

### ติดตั้งผ่าน HACS

1. เปิด HACS ใน Home Assistant
2. ไปที่เมนู 3 จุด > **Custom repositories**
3. เพิ่ม repository `batty211/smartlock-tuya` เป็นประเภท **Integration**
4. ค้นหาและติดตั้ง **Smart (Con)lock tuya**
5. Restart Home Assistant
6. ไปที่ **Settings** > **Devices & services** > **Add integration**
7. ค้นหา **Smart (Con)lock tuya**

### ติดตั้งเอง

1. คัดลอกโฟลเดอร์ `custom_components/smart_conlock_tuya` ไปไว้ใน `custom_components/` ของ Home Assistant
2. Restart Home Assistant
3. ไปที่ **Settings** > **Devices & services** > **Add integration**
4. ค้นหา **Smart (Con)lock tuya**

## วิธีตั้งค่าใน Home Assistant

Integration นี้ตั้งค่าผ่านหน้าจอ Home Assistant ไม่ต้องแก้ YAML

1. กรอก **Access ID**
2. กรอก **Access Secret**
3. เลือก **API Region** ให้ตรงกับ Data Center ของ Tuya IoT Project
4. เลือกอุปกรณ์ล็อกที่ต้องการเพิ่ม

ถ้ามีล็อกหลายตัว ให้เพิ่ม Integration แยกตามแต่ละอุปกรณ์

## แบตเตอรี่

Entity `sensor.<ชื่ออุปกรณ์>_battery` จะแสดงค่าสถานะแบตเตอรี่จาก Tuya:

เพื่อลดการใช้โควตา API ระบบจะไม่ถามแบตเตอรี่ถี่ ๆ โดยจะอัปเดตตอนเริ่มใช้งาน, วันละครั้ง, และตอนมี `Call Active`

| ค่าใน Tuya | ความหมายโดยประมาณ    |
| ---------- | -------------------- |
| `high`     | แบตสูง               |
| `medium`   | แบตกลาง              |
| `low`      | แบตต่ำ               |
| `poweroff` | แบตหมดหรืออุปกรณ์ดับ |

Entity นี้มี attribute `battery_percent_estimate` เป็นเปอร์เซ็นต์โดยประมาณ:

| ค่าใน Tuya | เปอร์เซ็นต์โดยประมาณ |
| ---------- | -------------------- |
| `high`     | 75                   |
| `medium`   | 50                   |
| `low`      | 20                   |
| `poweroff` | 0                    |

## รูปล่าสุดจากล็อกวิดีโอ

สำหรับ `jtmspro` จะมี `image.<ชื่ออุปกรณ์>_latest_image`

ถ้า Tuya ส่ง URL รูปที่ Home Assistant ใช้ได้ entity นี้จะแสดงรูปจากเหตุการณ์ล่าสุด เช่น คนกดกริ่งหรือเริ่มวิดีโอคอล

ถ้าไม่มีรูป อาจเกิดจาก:

- Tuya ไม่ส่ง URL รูปให้บัญชีนี้
- ต้องเปิดบริการ video/media เพิ่มใน Tuya IoT
- สื่อถูกเข้ารหัสหรือใช้ URL ที่ Home Assistant โหลดตรงไม่ได้
- อุปกรณ์รุ่นนั้นไม่รองรับการดึงรูปล่าสุดผ่าน API ที่เปิดอยู่

## อุปกรณ์ที่รองรับ

ทดสอบแล้วกับ:

- Conlock Xercon Curve
- Tuya Access Control category `mk`
- `jtmspro` video smart lock / Conlock ตามการใช้งานใน fork นี้

ควรใช้ได้กับอุปกรณ์ล็อก Tuya ที่รองรับ Smart Lock Cloud API เช่น:

- `mk`
- `ms`
- `jtmsbh`
- `jtmspro`
- `gyms`
- `hotelms`
- `videolock`
- `photolock`

ถ้าอุปกรณ์ของคุณไม่เจอในขั้นตอนตั้งค่า หรือกดปลดล็อกไม่ได้ ให้ตรวจสอบว่าอุปกรณ์นั้นอยู่ในบัญชี Tuya / Smart Life ที่ผูกกับ Tuya IoT Project แล้ว และเปิด Remote Unlock ในแอปแล้ว

## ข้อจำกัด

- ต้องใช้อินเทอร์เน็ต เพราะคำสั่งล็อก/ปลดล็อกส่งผ่าน Tuya Cloud
- ถ้า Tuya IoT API service หมดอายุ ต้องต่ออายุใน [iot.tuya.com](https://iot.tuya.com)
- สำหรับ `jtmspro` ปุ่มปลดล็อกจะกดได้เฉพาะตอนอุปกรณ์ออนไลน์และมี `Call Active`
- Integration รอเหตุการณ์จาก Tuya เป็นหลัก และไม่ถามประวัติเหตุการณ์ซ้ำ ๆ เพื่อกันโควตา API พุ่ง
- ถ้า Tuya ไม่ส่งเหตุการณ์กริ่ง/วิดีโอคอลมาให้ Home Assistant, `Call Active` จะไม่เปิดเอง
- `lock.<ชื่ออุปกรณ์>` ของ `jtmspro` เป็นปุ่มสั่งปลดล็อก ไม่ใช่สถานะล็อกจริง
- สถานะจริงของ `jtmspro` ให้ดูที่ `lock.<ชื่ออุปกรณ์>_physical_status`
- รูปหรือสื่อจากวิดีโอล็อกขึ้นกับสิทธิ์ API และข้อมูลที่ Tuya ส่งให้บัญชีของคุณ

## แก้ปัญหาเบื้องต้น

| อาการ                                       | วิธีตรวจสอบหรือแก้ไข                                                                   |
| ------------------------------------------- | -------------------------------------------------------------------------------------- |
| เพิ่ม Integration แล้วไม่เจออุปกรณ์         | ตรวจสอบว่าผูกบัญชี Tuya / Smart Life กับ Tuya IoT Project แล้ว และเลือก Region ถูกต้อง |
| ขึ้น `invalid_auth`                         | ตรวจ Access ID, Access Secret และ Region อีกครั้ง                                      |
| กดปลดล็อกแล้วไม่ทำงาน                       | เปิด Remote Unlock ในแอป Tuya / Smart Life                                             |
| ขึ้น error แนว permission หรือ path invalid | ตรวจว่าเปิด **IoT Core** และ **Smart Lock Open Service** แล้ว และบริการยังไม่หมดอายุ   |
| `jtmspro` กด Unlock ไม่ได้                  | ตรวจว่าอุปกรณ์ออนไลน์ และมี `Call Active` หลังจากกดกริ่งหรือเริ่มวิดีโอคอล             |
| ปุ่มหลักของ `jtmspro` ถูก disabled          | เป็นพฤติกรรมปกติเมื่อยังไม่มี `Call Active` หรืออุปกรณ์ offline                        |
| ปุ่มหลักของ `jtmspro` แสดง Unlock ตลอด      | เป็นพฤติกรรมที่ตั้งใจไว้ เพราะปุ่มนี้คือปุ่มสั่งปลดล็อกเท่านั้น                        |
| สถานะประตูจริงไม่ตรง                        | ปรับที่ `lock.<ชื่ออุปกรณ์>_physical_status`                                           |
| ไม่มีรูปจากวิดีโอล็อก                       | ตรวจสิทธิ์ video/media API ใน Tuya IoT และดูว่าอุปกรณ์ส่งรูปให้บัญชีนี้หรือไม่         |

## Credits

This project was forked from [`nicolasglg/tuya-smart-lock`](https://github.com/nicolasglg/tuya-smart-lock) so I could adapt it for the Conlock Xercon Curve smart lock that I use at home.

Many thanks to the original project and its author for the excellent foundation this fork is built on.

## License

This fork keeps the original upstream MIT License and copyright notice.
See [LICENSE](LICENSE) for details.
