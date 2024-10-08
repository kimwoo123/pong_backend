from asgiref.sync import sync_to_async
from django.http import JsonResponse
from django.core.cache import cache
from django.views import View
import json
import logging

from .utils import get_default_session_data
from .models import Game, Tournament
from auth.decorators import login_required


logger = logging.getLogger(__name__)


def validate_game(data, mode):
    errors = {}
    required_fields = ["player1Nick", "player2Nick", "player1Score", "player2Score", "mode"]
    for field in required_fields:
        if field not in data:
            errors[field] = f"{field} is required."
    if data.get("mode") != mode:
        errors["mode"] = f"Game mode must be '{mode}'"
    return errors


class GameView(View):
    @login_required
    async def get(self, request, decoded_jwt):
        user_id = decoded_jwt.get("user_id")
        page_number = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("size", 10))

        total_games = await sync_to_async(Game.objects.filter(user_id=user_id).count)()
        start = (page_number - 1) * page_size
        end = start + page_size
        games = await sync_to_async(list)(
            Game.objects.filter(user_id=user_id).order_by("-created_at")[start:end]
        )
        response_data = self.objects_to_dict(games)

        total_pages = (total_games + page_size - 1) // page_size
        has_next = page_number < total_pages
        has_previous = page_number > 1

        return JsonResponse(
            {
                "games": response_data,
                "page": {
                    "current": page_number,
                    "has_next": has_next,
                    "has_previous": has_previous,
                    "total_pages": total_pages,
                    "total_items": total_games,
                },
            },
        )

    @login_required
    async def post(self, request, decoded_jwt):
        user_id = decoded_jwt.get("user_id")
        try:
            data = json.loads(request.body)
            game = await sync_to_async(Game.objects.create)(
                user_id=user_id,
                player1_nick=data["player1Nick"],
                player2_nick=data["player2Nick"],
                player1_score=data["player1Score"],
                player2_score=data["player2Score"],
                mode=data["mode"],
            )
            return JsonResponse({"status": "Game created successfully", "id": game.id}, status=201)
        except KeyError as e:
            return JsonResponse({"error": f"Missing required field: {str(e)}"}, status=400)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)

    def objects_to_dict(self, game_list):
        return [
            {
                "id": game.id,
                "player1Nick": game.player1_nick,
                "player2Nick": game.player2_nick,
                "player1Score": game.player1_score,
                "player2Score": game.player2_score,
                "mode": game.mode,
                "tournament_id": game.tournament_id,
                "created_at": game.created_at.isoformat(),
            }
            for game in game_list
        ]


class SessionView(View):
    @login_required
    async def get(self, request, decoded_jwt):
        """
        캐시에 저장된 세션 정보 반환

        :query mode: 게임 모드 토너먼트 및 일반
        :cookie jwt: 인증을 위한 JWT
        """
        user_id = decoded_jwt.get("user_id")
        mode = request.GET.get("mode")
        if mode != "tournament":
            mode = "normal"
        default_data = get_default_session_data(user_id, mode)
        session_data = await cache.aget(f"session_data_{mode}_{user_id}", default_data)
        return JsonResponse(session_data)

    @login_required
    async def post(self, request, decoded_jwt):
        """
        tournament 플레이어 이름을 cache에 저장한 뒤
        불러와서 사용

        :body players_name: 사용자 이름 리스트
        :cookie jwt: 인증을 위한 JWT
        """
        try:
            body = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        user_id = decoded_jwt.get("user_id")
        session_data = get_default_session_data(user_id, "tournament")
        players_name = body.get("players_name", session_data.get("players_name"))
        session_data["players_name"] = players_name
        cache.set(f"session_data_tournament_{user_id}", session_data, 500)
        return JsonResponse({"message": "Set session success"})

    @login_required
    async def delete(self, request, decoded_jwt):
        """
        페이지 뒤로가기를 누를 시 세션데이터로 인한 오류 발생
        세션 데이터를 지우는 API

        :cookie jwt: 인증을 위한 JWT
        """
        user_id = decoded_jwt.get("user_id")
        try:
            body = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        mode = body.get("mode")
        if mode != "tournament":
            mode = "normal"
        cache.delete(f"session_data_{mode}_{user_id}")
        return JsonResponse({"message": "Delete session success"})
