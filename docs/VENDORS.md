# راهنمای فروشنده‌ها و ماتریس پشتیبانی

> همهٔ ستون‌ها یعنی «در شبیه‌ساز و تست‌های خودکار کار می‌کند». هیچ‌کدام روی سخت‌افزار/فریمور واقعی تأیید نشده. فرمت خروجی‌های سبک‌شده بر پایهٔ دانش من از CLIها نوشته شده و **باید در آزمایشگاه (PNETLab/ESXi) راستی‌آزمایی شود** ([LAB-TESTING.md](LAB-TESTING.md)).

## Cisco (IOS / IOS XE / NX-OS) — ترنسپورت: SSH
نیازها: کاربر با privilege 15 (یا enable secret). `show version` شناسایی را انجام می‌دهد؛ مدل‌های `C9*/WS-C/C2*/C3*` سوئیچ، `ISR/ASR/CSR/C8*` روتر (حدس) و نقش سوئیچ با probe واقعی `show vlan brief` تأیید می‌شود.
محدودیت‌ها: NX-OS شناسایی/ترجمهٔ پایه دارد ولی پایش ندارد؛ `allowed_hosts` SNMP اعمال نمی‌شود (ACL لازم است)؛ trunk روی IOS قدیمی `switchport trunk encapsulation dot1q` را خودکار نمی‌فرستد (هشدار می‌دهد)؛ NETCONF/RESTCONF استفاده نشده.

## MikroTik (RouterOS 6/7) — ترنسپورت: SSH CLI
شناسایی از `/system resource print`؛ نسخهٔ ۶ و ۷ برای NTP متفاوت ترجمه می‌شوند (v6 حداکثر دو سرور؛ حذف تکی ممکن نیست). افزودن‌ها با `:if ... find ... do={add}` ایدم‌پوتنت‌اند. بدون: Bridge VLAN filtering، سوئیچ‌پورت، MTU، فایروال/NAT/mangle، WireGuard/IPsec، صف‌ها، RouterOS API (8728/8729). `/export` رمزها را نمی‌دهد (پشتیبان باینری از SSH ممکن نیست).

## Fortinet (FortiOS) — ترنسپورت: SSH CLI
زیرواسط VLAN، مسیر ایستا (افزودن؛ **حذف نیازمند id و پیاده نشده**)، NTP، DNS، Syslog، SNMP (community با host مجاز)، hostname. بدون: سیاست فایروال، آبجکت‌ها، NAT، SD-WAN، VPN، HA (REST API استفاده نشده). VDOM ثابت `root`.

## ماتریس ماژول
| ماژول | Cisco | MikroTik | Fortinet |
|---|---|---|---|
| Hostname | ✔ | ✔ | ✔ |
| VLAN (database / SVI / sub-interface) | database + SVI | sub-interface | sub-interface |
| Interface: description/enable/IPv4 | ✔ | ✔ | ✔ |
| Interface: access/trunk/native/allowed | ✔ (سوئیچ) | ✘ | ✘ |
| Interface: MTU | ✔ | ✘ | ✔ |
| Static route (add / remove) | ✔ / ✔ | ✔ / ✔ | ✔ / ✘ |
| NTP | ✔ | ✔ (v6 partial) | ✔ (add only) |
| DNS | ✔ | ✔ | ✔ (add only) |
| Syslog | ✔ | ✔ (۱ سرور) | ✔ (۱ سرور) |
| SNMP v2c | ✔ | ✔ | ✔ |
| Save / Reboot | ✔ / ✔ | no-op / ✔ | no-op / ✔ |
| پشتیبان | running+startup | `/export` | full-configuration |
| CPU/Mem | IOS/XE | ✔ | ✔ |
| همسایه LLDP/CDP | ✔ | ✔ (IP neighbor) | ✘ |

اصلاً پیاده نشده (برای همهٔ فروشنده‌ها): OSPF/OSPFv3/EIGRP/BGP/RIP، VRF، PBR، route-map/prefix-list، ACL، NAT، سیاست/آبجکت فایروال، DHCP، QoS، STP/MSTP، LACP/EtherChannel، port-security، storm control، IPv6، AAA/RADIUS/TACACS، 802.1X، بی‌سیم، SNMPv3، کاربران سیستم، شمارنده‌ها/آمار ترافیک واسط، SNMP polling.
