from django.urls import path

from . import auth

urlpatterns = [
    path('info', auth.UserInfo.as_view(), name='user_info'),
    path('token', auth.OAuthView.as_view(), name='token'),
    path('otp/qrcode', auth.QRcodeView.as_view(), name='otp_qrcode'),
    path('otp/verify', auth.OTPView.as_view(), name='otp_verify'),
    path('login', auth.Login.as_view(), name='login'),
    path('test', auth.Test.as_view(), name='test'),
]