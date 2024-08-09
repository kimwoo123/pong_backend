from asgiref.sync import sync_to_async
from django.http import JsonResponse, HttpResponseRedirect
from django.core.cache import cache
from functools import wraps
from os import getenv
import aiohttp
import jwt

from authentication.models import User

API_URL = getenv("API_URL")
JWT_SECRET = getenv("JWT_SECRET")
INTRA_UID = getenv("INTRA_UID")
INTRA_SECRET_KEY = getenv("INTRA_SECRET_KEY")
REDIRECT_URI = getenv("REDIRECT_URI")
STATE = getenv("STATE")


def validate_jwt(request):
    """
    JWT 검증 및 디코딩 함수
    """
    encoded_jwt = request.COOKIES.get("jwt")
    if not encoded_jwt:
        return None, JsonResponse({"error": "No jwt in request"}, status=401)

    try:
        decoded_jwt = jwt.decode(encoded_jwt, JWT_SECRET, algorithms=["HS256"])
    except:
        return None, JsonResponse({"error": "Decoding jwt failed"}, status=401)

    return decoded_jwt, None


def auth_decorator_factory(check_otp=False):
    def decorator(func):
        @wraps(func)
        async def wrapper(self, request, *args, **kwargs):
            """
            클라이언트 인증 확인 데코레이터
            token만 확인하는 단계와 OTP를 확인하는 2가지 단계로 나뉨
            :param check_otp: OTP 통과 확인이 필요한지 나타내는 인자
            :header Authorization: access_token을 담은 JWT
            """
            decoded_jwt, error_response = validate_jwt(request)
            if error_response:
                return error_response

            user_id = decoded_jwt.get("user_id")
            if not user_id:
                return JsonResponse({"error": "No user id provided"}, status=401)

            if response := await token_refresh_if_invalid(request, decoded_jwt, user_id):
                return response

            otp_verified = decoded_jwt.get("otp_verified")
            if check_otp and otp_verified == False:
                return JsonResponse(
                    {
                        "error": "Need OTP authentication",
                        "otp_verified": otp_verified,
                        "show_otp_qr": user_data.get("is_verified"),
                    },
                    status=403,
                )

            if check_otp == False and otp_verified:
                return JsonResponse({"error": "Already passed OTP authentication"}, status=403)

            return await func(self, request, decoded_jwt, *args, **kwargs)

        return wrapper

    return decorator


async def token_refresh_if_invalid(request, decoded_jwt, user_id):
    user_data = await cache.aget(f"user_data_{user_id}")
    if user_data:
        return None
    tokens = await refresh_token(user_id)
    if not tokens:
        return JsonResponse({"error": "Need login"}, status=401)
    await set_refresh_token(user_id, tokens["refresh_token"])
    return await create_response(request, decoded_jwt, tokens)


async def refresh_token(user_id):
    refresh_token = await get_refresh_token(user_id)
    data = {
        "grant_type": "refresh_token",
        "client_id": INTRA_UID,
        "client_secret": INTRA_SECRET_KEY,
        "redirect_uri": REDIRECT_URI,
        "refresh_token": refresh_token,
        "state": STATE,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{API_URL}/oauth/token", data=data) as response:
                response_data = await response.json()
                if response.status != 200:
                    return None
                return {
                    "access_token": response_data.get("access_token"),
                    "refresh_token": response_data.get("refresh_token"),
                }
    except aiohttp.ClientError:
        return None


@sync_to_async
def get_refresh_token(user_id):
    user = User.objects.get(id=user_id)
    return user.refresh_token


@sync_to_async
def set_refresh_token(user_id, refresh_token):
    return User.objects.filter(id=user_id).update(refresh_token=refresh_token)


async def create_response(request, decoded_jwt, tokens):
    response = HttpResponseRedirect(request.get_full_path())
    encoded_jwt = jwt.encode(
        {
            "access_token": tokens["access_token"],
            "user_id": decoded_jwt.get("user_id"),
            "otp_verified": decoded_jwt.get("otp_verified"),
        },
        JWT_SECRET,
        algorithm="HS256",
    )
    response.set_cookie("jwt", encoded_jwt, httponly=True, secure=True)
    return response


login_required = auth_decorator_factory(check_otp=True)
token_required = auth_decorator_factory(check_otp=False)
