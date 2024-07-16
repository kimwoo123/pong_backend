from django.http import HttpResponse, JsonResponse, HttpResponseRedirect
from django.core.cache import cache
from django.views import View
from urllib.parse import urlencode
from os import getenv
import pyotp
import requests
import jwt
from datetime import timezone

from authentication.decorators import login_required
from authentication.models import User, OTPSecret

"""
42 OAuth2의 흐름
1. https://api.intra.42.fr/oauth/authorize 사용자를 연결한다.
2. 사용자가 권한을 부여하는 화면이 반환된다.
3. 제공된 client_id를 포함한 authorize URL을 사용한다.
4. 사용자가 권한을 부여하는경우, redirect uri로 리다이렉션 되며, "code"를 반환한다.
5. https://api.intra.42.fr/oauth/token URI에 POST요청으로
    { client_id, client_secret, code, redirect_uri }
    인자를 넘겨준다. 서버 측에서 보안 연결을 통해 수행되야 한다.
6. 받은 "code"를 활용하여 /oauth/token URI를 통해 access_token 을 반환받는다.
7. access_token을 header에 추가하여 API request를 구성한다.
    curl -H "Authorization: Bearer YOUR_ACCESS_TOKEN" /
        https://api.intra.42.fr/v2/me
"""

"""
backend 인증 로직
1. frontend에서 redirectURI를 통하여 얻은 "code"를 받는다
2. "code"를 access_token으로 exchange한다.
3. access_token을 사용하여 /v2/me 에서 정보를 받는다.
4. email 정보를 사용하여 2FA를 실행한다
5. 첫번째 로그인의 경우 OTP에 필요한 secret을 생성하고 
    URI로 QR code를 그린다
6. QR code를 사용해 google authenticator 등록
7. OTP 입력 및 검증
"""
TOKEN_EXIRES = 7200
CACHE_TIMEOUT = 900  # 15분
MAX_ATTEMPTS = 5
API_URL = getenv("API_URL")
JWT_SECRET = getenv("JWT_SECRET")
INTRA_UID = getenv("INTRA_UID")
INTRA_SECRET_KEY = getenv("INTRA_SECRET_KEY")
REDIRECT_URI = getenv("REDIRECT_URI")
STATE = getenv("STATE")


"""
TODO
OTP 주의사항
1. HTTPS 통신 사용
2. OTP브루트포스 방지 (타임스탬프 확인)
3. 스로틀 속도 제한
"""

class OAuthView(View):

    # TODO: code를 access_token으로 바꾼 후 get_user_info 사용 및 cache저장
    def get(self, request):
        """
        frontend에서 /oauth/authorize 경로로 보낸 후 redirection되어서 오는 곳.
        querystring으로 code를 가져온 후 code를 access_token으로 교환
        access_token을 cache에 저장해서 expires_in을 체크한다.
        """
        code = request.GET.get('code')
        if not code:
            return JsonResponse({"error": "No code value in querystring"}, status=400)
        data = {
            "grant_type": "authorization_code",
            "client_id": INTRA_UID,
            "client_secret": INTRA_SECRET_KEY,
            "code": code,
            "redirect_uri": getenv("REDIRECT_URI"),
            "state": getenv("STATE"),
        }
        try:
            response = requests.post(f'{API_URL}/oauth/token', data=data)
            response_data = response.json()
            if response.status_code != 200:
                return JsonResponse(response_data, status=response.status_code)

            token = response_data.get("access_token")
            # expires_in = response_data.get("expires_in")
            if not token:
                return JsonResponse({"error": "No access token in response"}, status=400)
            encoded_jwt = jwt.encode({"access_token": token}, JWT_SECRET, algorithm="HS256")
            return JsonResponse({"jwt": encoded_jwt}, status=200)

        except requests.RequestException as e:
            error_message = {"error": str(e)}
            return JsonResponse(error_message, status=500)



def redirect(self):
    """
    42intra로 redirect해서 로그인 할 경우 정보를 반환
    frontend에서 받은 정보를 backend에 전달해야 하는 로직
    """
    params = {
        "client_id": INTRA_UID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "public",
        "state": STATE,
    }
    base_url = API_URL + "/oauth/authorize"
    encoded_params = urlencode(params)
    URI = f"{base_url}?{encoded_params}"
    response = requests.get(URI)
    print(response.text)
    return HttpResponseRedirect(URI)


def get_user_info(request):
    """
    access_token을 활용하여 user의 정보를 받아온다.
    정보를 받아와서 db에 있는지 확인한 후 없을 경우 생성
    OTP Secret값 생성도 필요
    """
    encoded_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3NfdG9rZW4iOiIzNTYyNDUxMjVhNjQ4YzY0YTg0YmY3MjI1MDhjY2VkNWEzOTQ1Njg1YzQ4MzEzZWNhNDFhYTdkYjI4N2U2YTVhIn0.T6D3v7fq-0PK-G1y2tc_I0hqav1YJpbHidbXCXBxqfk"
    decoded_jwt = jwt.decode(encoded_jwt, JWT_SECRET, algorithms=["HS256"])
    access_token = decoded_jwt.get("access_token")
    headers = { "Authorization": "Bearer %s" % decoded_jwt.get("access_token") }
    response = requests.get(f'{API_ULR}/v2/me', headers=headers)
    if response.status_code == 200:
        data = response.json()
        user, _ = User.objects.get_or_create(
            id = data['id'],
            defaults = {
                'email': data['email'],
                'login': data['login'],
                'usual_full_name': data['usual_full_name'],
                'image_link': data['image']['link'],
            }
        )
        user_data = {
            'id': user.id,
            'email': user.email,
            'login': user.login,
            'usual_full_name': user.usual_full_name,
            'image_link': user.image_link,
        }
        cache.set(f'user_data_{access_token}', user_data, TOKEN_EXIRES)
        return HttpResponse(response.text)
    return JsonResponse(response.json(), status=response.status_code)

# @login_required
def otp_test(request):
    """
    otp URI를 만들고 이를 QRcode로 변환하여 사용
    secret key 값을 user db에 저장한 뒤 꺼내어서 사용
    :request secret: pyotp secret of user info
    """
    # TODO: need to store secret value in user db
    secret = pyotp.random_base32()
    URI = pyotp.totp.TOTP(secret).provisioning_uri(
        # TODO: user email로 입력
        name="user@mail.com", issuer_name="pong_game"
    )
    return JsonResponse({"otpauth_uri": URI}, status=200)


# @login_required
def validate_otp(request):
    """
    user의 secret값을 사용해서 otp 값이 타당한지 확인
    secret을 db에서 매번 확인하는 것, caching 하는 것 선택
    """
    # TODO: db? caching?
    secret = "temp"
    input_pass = request.POST.get("otp")
    expected_pass = pyotp.TOTP(secret).now() # type str
    if input_pass != expected_pass:
        return HttpResponse("bad")
    return HttpResponse("good")
    

@login_required
def need_login(request):
    return HttpResponse("Can you join")




def get_token_info(request):
    """
    access_token을 발급 받고 테스트
    expired 및 status_code를 사용해 유효한지 확인
    :request jwt: acces_token이 담긴 jwt
    """
    URI = API_URL + "/auth/token/info"
    encoded_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3NfdG9rZW4iOiI0MjQyYjk1YWY4MzBiNzI1MjllZmJkZTA2MDI5OTAxYWEyZTA4YmY3ZDRlYmMzMzIwYjI1ZmIzNGUyMjllZWFhIn0.eIivOHCPQVxtIouADQss3re6yrlSlWtCycGCUKss4QE"
    decoded_jwt = jwt.decode(encoded_jwt, JWT_SECRET, algorithms=["HS256"])
    token = decoded_jwt.get("access_token")
    headers = { "Authorization": "Bearer %s" % token }
    response = requests.get(f'{API_URL}/auth/token/info', headers=headers)
    return HttpResponse(response.text)


def temp_access_token(request):
    """
    테스트용 exchange_access_token
    grant_type이 client_credentials으로
    제한된 사용이 가능한 access_token 발급
    """

    data = {
        "grant_type": "client_credentials",
        "client_id": INTRA_UID,
        "client_secret": INTRA_SECRET_KEY,
    }
    try:
        response = requests.post(f'{API_URL}/oauth/token', data=data)
        response_data = response.json()
        if response.status_code != 200:
            return JsonResponse(response_data, status=response.status_code)
        
        token = response_data.get("access_token")
        expires_in = response_data.get("expires_in")
        if not token or not expires_in:
            error_message = {"error": "No access_token or expires_in in response"}
            return JsonResponse(error_message, status=400)
        JWT_SECRET = getenv("JWT_SECRET")
        encoded_jwt = jwt.encode({"access_token": token}, JWT_SECRET, algorithm="HS256")
        return JsonResponse({"jwt": encoded_jwt}, status=200)

    except requests.RequestException as e:
        error_message = {"error": str(e)}
        return JsonResponse(error_message, status=500)



def get_otp_data(user_id):
    """
    otp data를 받아옴
    cache 확인 후 DB 확인
    없을 경우 OTP 정보 입력 필요
    """
    cache_key = f"otp_data_{user_id}"
    data = cache.get(cache_key)
    if data is None:
        try:
            otp_secret = OTPSecret.objects.get(user_id=user_id)
            data = {
                'secret': otp_secret.secret,
                'attempts': otp_secret.attempts,
                'last_attempt': otp_secret.last_attempt,
                'is_locked': otp_secret.is_locked
            }
            cache.set(cache_key, data, CACHE_TIMEOUT)
        except OTPSecret.DoesNotExist:
            return None
    return data

def update_otp_data(user_id, data):
    """
    OTP 시도 횟수 및 시간 저장
    5회 이상 시도 시 계정 잠금 및 초기화 시간 900초 소요
    """
    cache_key = f"otp_data_{user_id}"
    cache.set(cache_key, data, CACHE_TIMEOUT)

    if data['attempts'] % 5 == 0 or data['is_locked']:
        OTPSecret.objects.filter(user_id=user_id).update(
            attempts=data['attempts'],
            last_attempt=data['last_attempt'],
            is_locked=data['is_locked']
        )

def verify_otp(user_id, otp_code):
    """
    OTP 시도 할 경우 함수
    OTP 정보 확인 및 900초 지났을 경우 시도 횟수 초기화
    계정 잠금, 정보 없음(?), OTP인증 실패 확인
    """
    otp_data = get_otp_data(user_id)
    if not otp_data:
        return False, "OTP 설정을 찾을 수 없습니다."

    if otp_data['is_locked']:
        return False, "계정이 잠겼습니다. 관리자에게 문의하세요."

    now = timezone.now()
    if otp_data['last_attempt'] and (now - otp_data['last_attempt']).total_seconds() > CACHE_TIMEOUT:
        otp_data['attempts'] = 0

    otp_data['attempts'] += 1
    otp_data['last_attempt'] = now

    if otp_data['attempts'] >= MAX_ATTEMPTS:
        otp_data['is_locked'] = True
        update_otp_data(user_id, otp_data)
        return False, "최대 시도 횟수를 초과했습니다. 15분 후에 다시 시도하세요."

    if pyotp.TOTP(otp_data['secret']).verify(otp_code):
        otp_data['attempts'] = 0
        otp_data['is_locked'] = False
        update_otp_data(user_id, otp_data)
        return True, "OTP 인증 성공"

    update_otp_data(user_id, otp_data)
    return False, f"잘못된 OTP 코드입니다. 남은 시도 횟수: {MAX_ATTEMPTS - otp_data['attempts']}"