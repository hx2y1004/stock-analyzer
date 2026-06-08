# 토스 API 고정 IP 프록시 설정 (Oracle Cloud 무료 VM + tinyproxy)

> **목적**: Railway egress IP가 재배포마다 바뀌어 토스 401이 나는 문제 해결.
> 고정 IP 프록시를 한 번 만들어 토스 콘솔에 IP 1회 등록하면 영구 해결.
> 앱은 `TOSS_PROXY_URL`만 설정하면 모든 토스 호출을 이 프록시로 경유함.

---

## 1. Oracle Cloud 무료 VM 생성

1. https://cloud.oracle.com 가입 (카드 등록 필요하지만 **Always Free는 과금 안 됨**)
2. 좌측 메뉴 → **Compute → Instances → Create Instance**
3. 설정:
   - **Image**: Canonical Ubuntu 22.04
   - **Shape**: `VM.Standard.E2.1.Micro` (Always Free eligible 표시 확인)
   - **SSH keys**: "Generate a key pair" → **private key 다운로드** (꼭 저장)
4. **Create** 클릭 → 1~2분 후 생성됨
5. 인스턴스 상세에서 **Public IP address** 확인 (예: `140.238.x.x`)

### 1-b. 공인 IP를 고정(예약)으로 — 재부팅해도 유지
- 인스턴스 → Networking → 가상 NIC → IPv4 → ephemeral IP 옆 편집
- **"Reserved public IP"로 전환** (Always Free에 예약 IP 1개 포함)

---

## 2. SSH 접속

```bash
# 다운로드한 키 권한 설정 (Mac/Linux/WSL/Git Bash)
chmod 400 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<VM_PUBLIC_IP>
```
Windows PowerShell이면:
```powershell
ssh -i C:\path\to\ssh-key.key ubuntu@<VM_PUBLIC_IP>
```

---

## 3. tinyproxy 설치 + 설정 (BasicAuth로 보안)

SSH 접속한 상태에서:
```bash
# ⚠️ Oracle Ubuntu는 기본 IPv6 우선이라 apt가 IPv6로 나가다 timeout 됨 → IPv4 강제
sudo apt-get -o Acquire::ForceIPv4=true update
sudo apt-get -o Acquire::ForceIPv4=true install -y tinyproxy

# ⚠️ 비밀번호는 영숫자만! tinyproxy 파서가 ! @ # 등 특수문자에서 syntax error 냄
# (DefaultErrorFile/StatFile/ViaProxyName 등 따옴표 필요한 줄도 빼는 게 안전)
sudo tee /etc/tinyproxy/tinyproxy.conf >/dev/null <<'EOF'
User tinyproxy
Group tinyproxy
Port 8888
Timeout 600
LogLevel Info
MaxClients 50
Allow 0.0.0.0/0
BasicAuth tossuser CHANGE_ME_ALNUM_ONLY
EOF

# ⚠️ 기본 유닛은 Type=forking 인데 daemonize/pidfile 처리가 어긋나 "activating"에서 멈춤
#    → Type=simple + foreground(-d) override 로 고정
sudo mkdir -p /etc/systemd/system/tinyproxy.service.d
sudo tee /etc/systemd/system/tinyproxy.service.d/override.conf >/dev/null <<'EOF'
[Service]
Type=simple
ExecStart=
ExecStart=/usr/bin/tinyproxy -d
EOF

sudo systemctl daemon-reload
sudo systemctl enable tinyproxy
sudo systemctl restart tinyproxy
sudo systemctl is-active tinyproxy   # → active 확인
```
> `Allow 0.0.0.0/0` + **BasicAuth로 보호**(아무나 못 씀). 비번은 강력하되 **영숫자만** 사용.

---

## 4. 방화벽 — 포트 8888 열기 (두 곳 모두!)

### 4-a. Oracle 클라우드 방화벽 (Security List)
- 인스턴스 → Virtual Cloud Network → Security Lists → Default Security List
- **Add Ingress Rule**:
  - Source CIDR: `0.0.0.0/0`
  - IP Protocol: TCP
  - Destination Port Range: `8888`

### 4-b. VM 내부 방화벽 (Ubuntu on Oracle은 기본 차단)
> ⚠️ Oracle Ubuntu의 INPUT 체인엔 끝부분에 **REJECT(catch-all)** 규칙이 있음.
> 8888 ACCEPT를 그 **REJECT보다 앞**에 넣어야 함 (뒤에 넣으면 무용지물).
```bash
# 현재 규칙에서 REJECT 줄 번호 확인 (보통 5번)
sudo iptables -L INPUT -n --line-numbers
# REJECT 바로 앞 위치(예: 5)에 삽입
sudo iptables -I INPUT 5 -p tcp --dport 8888 -j ACCEPT
sudo netfilter-persistent save
# 확인: 8888 ACCEPT 가 REJECT 보다 위에 있어야 함
sudo iptables -L INPUT -n --line-numbers
```

---

## 5. 프록시 동작 테스트 (로컬 PC에서)

```bash
curl -x http://tossuser:CHANGE_ME_STRONG@<VM_PUBLIC_IP>:8888 https://api.ipify.org
# → <VM_PUBLIC_IP> 가 출력되면 성공 (프록시 경유 확인)
```

---

## 6. 토스 콘솔에 VM IP 등록
- 토스 개발자 콘솔 → Open API → **허용 IP 관리 → IP 추가**
- `<VM_PUBLIC_IP>` 등록
- (기존 Railway IP는 삭제해도 됨 — 이제 프록시 경유라 불필요)

---

## 7. Railway 환경변수 설정
Railway → web 서비스 → Variables:
```
TOSS_PROXY_URL = http://tossuser:CHANGE_ME_STRONG@<VM_PUBLIC_IP>:8888
```
저장 → 자동 재배포

---

## 8. 검증
배포 후:
```
https://<앱주소>/api/debug/toss
```
- `toss_proxy_set: true`
- `proxy_egress_ip: <VM_PUBLIC_IP>`  ← 토스에 등록한 IP와 일치해야 함
- `toss_token_ok: true`
- `toss_fx_usdkrw: <환율>`

이제 **재배포해도 토스가 보는 IP는 항상 VM IP** → 다시는 안 바뀜. ✅

---

## 참고
- 프록시 미설정(`TOSS_PROXY_URL` 비움) 시 앱은 직접 연결 + 실패 시 yfinance 폴백 (정상 동작)
- VM은 토스 호출만 중계 — 트래픽 적어 무료 한도 충분
- 보안: BasicAuth 비번 강력하게. 포트 8888은 열려있지만 인증 없이는 사용 불가
