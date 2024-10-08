from asgiref.sync import sync_to_async
from django.http import JsonResponse, HttpResponseRedirect
from django.core.cache import cache
from django.utils import timezone
from django.views import View
from django.db import transaction, DatabaseError
from os import getenv
from datetime import timedelta
import aiohttp
import pyotp
import jwt
import json
import logging

from .decorators import (
    token_required,
    login_required,
    refresh_access_token,
)
from .models import User, OTPSecret, OTPLockInfo
from .utils import get_user_data
from common.constants import *


logger = logging.getLogger(__name__)

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
1. OAuthView에 GET요청을 보낸다.
2. redirectURI를 통하여 "code"를 query parameter로 받는다 
2. "code"를 access_token으로 exchange한다.
3. access_token을 사용하여 /v2/me 에서 정보를 받는다.
4. email, secret 정보를 사용하여 2FA를 실행한다
5. 첫번째 로그인의 경우 OTP에 필요한 secret을 생성하고
    URI로 QR code를 그린다
6. QR code를 사용해 google authenticator 등록
7. OTP 입력 및 검증
"""


class OAuthView(View):
    async def get(self, request):
        """
        code값을 token으로 변환한 후
        http header에 cookie를 저장하여 반환
        frontend의 main페이지로 리다이렉션

        :query code: 42OAuth에서 받음 code값
        """
        code = request.GET.get("code")
        tokens = await self.exchange_code_for_token(code)
        if not tokens:
            return JsonResponse({"error": "Failed to obtain token"}, status=400)

        success, user_info = await self.get_user_info(tokens)
        if not success:
            return JsonResponse({"error": user_info}, status=500)

        encoded_jwt = self.create_jwt_token(tokens["access_token"], user_info["user"].id)
        redirect_url = self.get_redirect_url(
            # TODO: it can be shrink
            user_info["otp"].need_otp,
            user_info["otp"].is_verified,
        )
        return self.create_redirect_response(redirect_url, encoded_jwt)

    @login_required
    async def delete(self, request, decoded_jwt):
        """
        cache에 저장된 유저 정보 및 OTP패스 정보 폐기
        cookie JWT 폐기 및 홈 화면으로 리다이렉션

        :cookie jwt: 인증을 위한 JWT
        """
        user_id = decoded_jwt.get("user_id")
        cache.delete(f"user_data_{user_id}")
        response = JsonResponse({"message": "logout success"})
        response.delete_cookie("jwt")
        return response

    def get_redirect_url(self, need_otp, is_verified):
        if need_otp == True:
            if is_verified == False:
                return FRONT_BASE_URL + "/QRcode"
            else:
                return FRONT_BASE_URL + "/OTP"
        else:
            return FRONT_BASE_URL + "/main"

    def extract_code(self, request):
        body = json.loads(request.body.decode("utf-8"))
        return body.get("code")

    async def exchange_code_for_token(self, code):
        """42API에서 유저 정보를 받아온다"""
        data = {
            "grant_type": "authorization_code",
            "client_id": INTRA_UID,
            "client_secret": INTRA_SECRET_KEY,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "state": STATE,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{API_URL}/oauth/token", data=data) as response:
                    if response.status != 200:
                        return None
                    response_data = await response.json()
                    return {
                        "access_token": response_data.get("access_token"),
                        "refresh_token": response_data.get("refresh_token"),
                    }
        except aiohttp.ClientError:
            return None

    async def get_user_info(self, tokens):
        """
        access_token을 활용하여 user의 정보를 받아온다
        정보를 받아와서 user db에 있는지 확인한 후 없을 경우 생성
        """
        headers = {"Authorization": f'Bearer {tokens["access_token"]}'}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{API_URL}/v2/me", headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        return await self.process_user_data(data, tokens)
                    return False, await response.json()
        except aiohttp.ClientError as e:
            return False, str(e)

    @sync_to_async
    def process_user_data(self, data, tokens):
        """
        사용자 데이터 처리 및 OTP 데이터 생성
        필요한 정보는 cache에 저장
        """
        try:
            with transaction.atomic():
                user_data = self.update_or_create_user(data, tokens["refresh_token"])
                otp_data, created = self.get_or_create_otp_secret(data["id"])
                if created:
                    self.create_otp_lock_info(otp_data)
            self.set_cache(user_data, otp_data, tokens)
            return True, {"user": user_data, "otp": otp_data}
        except DatabaseError as e:
            return False, str(e)
        except transaction.TransactionManagementError as e:
            return False, str(e)

    def set_cache(self, user_data, otp_data, tokens):
        cache_value = {
            "email": user_data.email,
            "login": user_data.login,
            "secret": otp_data.secret,
            "is_verified": otp_data.is_verified,
            "need_otp": otp_data.need_otp,
        }
        cache.set(f"user_data_{user_data.id}", cache_value, TOKEN_EXPIRES)

    def update_or_create_user(self, data, refresh_token):
        user, _ = User.objects.update_or_create(
            id=data["id"],
            defaults={
                "email": data["email"],
                "login": data["login"],
                "usual_full_name": data["usual_full_name"],
                "image_link": data["image"]["link"],
                "refresh_token": refresh_token,
            },
        )
        return user

    def get_or_create_otp_secret(self, user_id):
        otp_secret, created = OTPSecret.objects.get_or_create(
            user_id=user_id,
            defaults={
                "secret": pyotp.random_base32(),
                "is_verified": False,
                "need_otp": True,
            },
        )
        return otp_secret, created

    def create_otp_lock_info(self, otp_data):
        OTPLockInfo.objects.create(
            otp_secret=otp_data,
        )

    def create_jwt_token(self, access_token, user_id):
        return jwt.encode(
            {
                "custom_exp": (timezone.now() + timedelta(seconds=JWT_EXPIRED)).timestamp(),
                "access_token": access_token,
                "user_id": user_id,
                "otp_verified": False,
            },
            JWT_SECRET,
            algorithm="HS256",
        )

    def create_redirect_response(self, redirect_url, jwt):
        response = HttpResponseRedirect(redirect_url)
        response.set_cookie("jwt", jwt, httponly=True, secure=True, samesite="Lax")
        return response


class QRcodeView(View):
    @token_required
    async def get(self, request, decoded_jwt):
        """
        QRcode에 필요한 secret값을 포함한 URI를 반환하는 함수
        한 번 OTP인증에 성공한 경우 다시 QR코드를 반환하지 않음

        :cookie jwt: 인증을 위한 JWT
        """
        user_id = decoded_jwt.get("user_id")
        try:
            user_data = await get_user_data(user_id)
            if user_data["is_verified"] == True:
                return JsonResponse({"error": "Can't show QRcode"}, status=400)
            uri = self.generate_otp_uri(user_data)
            return JsonResponse({"otpauth_uri": uri}, status=200)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=400)

    def generate_otp_uri(self, user_data):
        return pyotp.totp.TOTP(user_data["secret"]).provisioning_uri(
            name=user_data["email"], issuer_name="pong_game"
        )


class OTPView(View):
    @token_required
    async def post(self, request, decoded_jwt):
        """
        OTP 패스워드를 확인하는 view
        OTP 정보 확인 및 900초 지났을 경우 시도 횟수 초기화
        계정 잠금, 정보 없음, OTP인증 실패 확인

        cache를 사용하여 저장할 경우 퍼포먼스의 이득을 볼 수 있지만
        데이터의 정합성을 위해서 db를 확인한다

        :cookie jwt: 인증을 위한 JWT
        :body input_password: 사용자가 입력한 OTP
        """
        user_id = decoded_jwt.get("user_id")
        otp_data = await self.get_otp_data(user_id)
        if not otp_data:
            return JsonResponse({"error": "Can't found OTP data."}, status=500)

        now = timezone.now()
        if self.is_account_locked(otp_data, now):
            return JsonResponse({"error": "Account is locked. try later"}, status=403)

        otp_data["attempts"] += 1
        otp_data["last_attempt"] = now
        if otp_data["attempts"] >= MAX_ATTEMPTS:
            otp_data["is_locked"] = True
            await sync_to_async(self.update_otp_data)(user_id, otp_data)
            return JsonResponse(
                {
                    "error": "Maximum number of attempts exceeded. Please try again after 15 minutes."
                },
                status=403,
            )

        if self.verify_otp(request, otp_data["secret"]):
            await self.update_otp_success(user_id, otp_data)
            return await self.create_success_response(decoded_jwt)

        await sync_to_async(self.update_otp_data)(user_id, otp_data)
        return self.password_fail_response(otp_data["attempts"])

    async def create_success_response(self, decoded_jwt):
        response = JsonResponse({"success": "OTP authentication verified"})
        encoded_jwt = jwt.encode(
            {
                "custom_exp": (timezone.now() + timedelta(seconds=JWT_EXPIRED)).timestamp(),
                "access_token": decoded_jwt.get("access_token"),
                "user_id": decoded_jwt.get("user_id"),
                "otp_verified": True,
            },
            JWT_SECRET,
            algorithm="HS256",
        )
        response.set_cookie("jwt", encoded_jwt, httponly=True, secure=True, samesite="Lax")
        return response

    @sync_to_async
    def get_otp_data(self, user_id):
        try:
            otp_secret = OTPSecret.objects.select_related("otplockinfo").get(user_id=user_id)
            data = {
                "secret": otp_secret.secret,
                "attempts": otp_secret.otplockinfo.attempts,
                "last_attempt": otp_secret.otplockinfo.last_attempt,
                "is_locked": otp_secret.otplockinfo.is_locked,
                "is_verified": otp_secret.is_verified,
            }
        except OTPSecret.DoesNotExist:
            return None
        return data

    def password_fail_response(self, attempts):
        return JsonResponse(
            {
                "error": "Incorrect password.",
                "remain_attempts": MAX_ATTEMPTS - attempts,
            },
            status=400,
        )

    def is_account_locked(self, otp_data, now):
        if otp_data["is_locked"]:
            if (
                otp_data["last_attempt"]
                and (now - otp_data["last_attempt"]).total_seconds() > LOCK_ACCOUNT
            ):
                otp_data["is_locked"] = False
                otp_data["attempts"] = 0
                return False
            return True
        return False

    def verify_otp(self, request, secret):
        body = json.loads(request.body.decode("utf-8"))
        otp_code = body.get("input_password")
        return pyotp.TOTP(secret).verify(otp_code)

    @sync_to_async
    def update_otp_success(self, user_id, otp_data):
        otp_data["attempts"] = 0
        otp_data["is_locked"] = False
        otp_data["is_verified"] = True
        self.update_otp_data(user_id, otp_data)

    def update_otp_data(self, user_id, data):
        """
        OTP 시도 횟수 및 시간 저장
        5회 이상 시도 시 계정 잠금 및 초기화 시간 900초 소요
        """
        with transaction.atomic():
            otp_secret = OTPSecret.objects.select_for_update().get(user_id=user_id)

            # TODO: if not changed, do not hit query
            # OTPSecret 업데이트
            otp_secret.is_verified = data["is_verified"]
            otp_secret.save()

            # OTPLockInfo 업데이트
            otp_lock_info = OTPLockInfo.objects.get(otp_secret=otp_secret)
            otp_lock_info.attempts = data["attempts"]
            otp_lock_info.last_attempt = data["last_attempt"]
            otp_lock_info.is_locked = data["is_locked"]
            otp_lock_info.save()


class LoginView(View):
    async def get(self, request):
        return HttpResponseRedirect(AUTH_PAGE)


class StatusView(View):
    async def get(self, request):
        """
        유저의 인증 상태를 반환하는 함수

        :cookie jwt: 인증을 위한 JWT
        """
        encoded_jwt = request.COOKIES.get("jwt")
        if not encoded_jwt:
            return JsonResponse({"error": "No jwt in request"}, status=401)

        try:
            decoded_jwt = jwt.decode(encoded_jwt, JWT_SECRET, algorithms=["HS256"])
        except:
            return JsonResponse({"error": "Decoding jwt failed"}, status=401)

        otp_verified = decoded_jwt.get("otp_verified")
        return JsonResponse(
            {"access_token_valid": True, "otp_authenticated": otp_verified}, status=200
        )


class UserInfo(View):
    @login_required
    async def get(self, request, decoded_jwt):
        """
        main 화면에서 보여줄 유저 정보를 반환하는 API

        :cookie jwt: 인증을 위한 JWT
        """
        user_id = decoded_jwt.get("user_id")
        user_info = await get_user_data(user_id)
        # TODO: can modify?
        if not user_info:
            return JsonResponse({"error": "Invalid token"}, status=401)
        data = {
            "email": user_info["email"],
            "login": user_info["login"],
        }
        return JsonResponse(data, status=200)
