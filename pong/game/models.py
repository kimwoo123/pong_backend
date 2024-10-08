from django.db import models
from auth.models import User

GAME_MODES = [("1ON1", "1on1"), ("TOURNAMENT", "Tournament")]


class Game(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=False)
    player1_nick = models.CharField(max_length=10)
    player2_nick = models.CharField(max_length=10)
    player1_score = models.IntegerField()
    player2_score = models.IntegerField()
    mode = models.CharField(max_length=10, choices=GAME_MODES)
    tournament = models.ForeignKey("Tournament", on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Tournament(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=False)
    game1 = models.ForeignKey(
        "Game", related_name="tournament_game1", on_delete=models.SET_NULL, null=True
    )
    game2 = models.ForeignKey(
        "Game", related_name="tournament_game2", on_delete=models.SET_NULL, null=True
    )
    game3 = models.ForeignKey(
        "Game", related_name="tournament_game3", on_delete=models.SET_NULL, null=True
    )
