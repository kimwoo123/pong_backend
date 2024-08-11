# chat/consumers.py
import json
import numpy as np
import asyncio
import math

from channels.generic.websocket import AsyncWebsocketConsumer


class GameConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        self.game_task = None
        self.key_input = None
        self.mode = self.scope['url_route']['kwargs']['mode']
        self.session_data = self.get_session_data()
        await self.send(text_data=json.dumps({
            "type": "init_data",
            "left_score": self.session_data['left_score'],
            "right_score": self.session_data['right_score'],
            "players_name": self.get_players_name(),
        }))
        self.game = PongGame(self.send_callback, self.session_data)

    async def disconnect(self, close_code):
        if self.game_task:
            self.game_task.cancel()
        # TODO db 작업 및 세션 정리
        self.save_game_state()
        # await self.cleanup_resources()

    def save_game_state(self):
        if self.mode == "tournament":
            request.session['game_info_t'] = self.session_data
        else:
            request.session['game_info_n'] = self.session_data

    async def receive(self, text_data):
        if text_data == "start":
            # self.game.init_game()
            self.start_game()
        else:
            # TODO: MODIFY FLOW
            # self.game.proccess_key_input(text_data)
            self.key_input = json.loads(text_data)

    async def send_callback(self, data):
        await self.send(text_data=json.dumps(data))

    async def game_loop(self):
        try:
            while True:
                if self.key_input:
                    self.game.process_key_input(self.key_input)
                    self.key_input = None
                self.game.move_panels()
                await self.game.update()
                await asyncio.sleep(0.006)
        except asyncio.CancelledError:
            print("CancelledError")

    def start_game(self):
        self.game_task = asyncio.create_task(self.game_loop())

    def get_session_data(self):
        if self.mode == 'tournament':
            game_info = request.session.get('game_info_t', {})
        else:
            game_info = request.session.get('game_info_n', {})
        
        context = {
            'username': game_info.get('username', ['player1', 'player2', 'player3', 'player4']),
            'left_score': game_info.get('left_score', 0),
            'right_score': game_info.get('right_score', 0),
            'game_mode': game_info.get('game_mode', 'normal'),
            'game_round': game_info.get('round', 1),
            'win_history': game_info.get('win_history', []),
        }
        return context

    def get_players_name(self):
        name_list = self.session_data['username']
        game_round = self.session_data['game_round']
        if game_round == 1:
            return name_list[0], name_list[1]
        if game_round == 2:
            return name_list[2], name_list[3]
        if game_round == 3:
            return self.session_data['win_history']
        return 'player1', 'player2'

KEY_MAPPING = {
    "KeyW": 0,
    "KeyA": 1,
    "KeyS": 2,
    "KeyD": 3,
    "ArrowUp": 4,
    "ArrowDown": 6,
    "ArrowLeft": 5,
    "ArrowRight": 7,
}
GAME_END_SCORE = 3


class PongGame:
    def __init__(self, send_callback, session_data):
        self.send_callback = send_callback
        self.ball_pos = np.array([0.0, 0.0, 0.0])  # 공위치
        self.ball_vec = np.array([0.0, 0.0, 1.0])  # 공이 움직이는 방향
        self.ball_rot = np.array([0.0, 0.0, 0.0])  # 공의 회전벡터
        self.panel1_pos = np.array([0.0, 0.0, 50.0])  # panel1의 초기위치
        self.panel2_pos = np.array([0.0, 0.0, -50.0])  # panel2의 초기위치
        # self.flag = True # 공이 날라가는 방향

        # 키입력값 [W, A, S, D, UP, Left, Down, Right]
        self.key_state = [False, False, False, False, False, False, False, False]

        # 골대쪽 벽면말고 사이드에 있는 4개의 plane들을 의미하며 각각([법선벡터], 원점으로부터의 거리)를 가지고 있다.
        self.planes = [
            (np.array([1, 0, 0]), 10),
            (np.array([-1, 0, 0]), 10),
            (np.array([0, 1, 0]), 10),
            (np.array([0, -1, 0]), 10),
        ]

        # panel이 위치한 평면
        self.panel1_plane = (np.array([0, 0, -1]), 50)  # (법선벡터, 원점과의 거리)
        self.panel2_plane = (np.array([0, 0, 1]), 50)
        self.game_state = "playing"
        self.winner = None
        self.session_data = session_data
        self.mode = session_data.get('game_mode', 'normal')
        self.player1_score = session_data.get('left_score')
        self.player2_score = session_data.get('right_score')

    def init_game(self):
        self.ball_pos = np.array([0.0, 0.0, 0.0])  # 공위치
        self.ball_vec = np.array([0.0, 0.0, 1.0])  # 공이 움직이는 방향
        self.ball_rot = np.array([0.0, 0.0, 0.0])  # 공의 회전벡터
        self.panel1_pos = np.array([0.0, 0.0, 50.0])  # panel1의 초기위치
        self.panel2_pos = np.array([0.0, 0.0, -50.0])  # panel2의 초기위치

    def process_key_input(self, key_input):
        for k, v in key_input.items():
            if k in KEY_MAPPING:
                self.key_state[KEY_MAPPING[k]] = v

    def move_panels(self):
        ball_speed = 0.2
        if self.key_state[0]:
            self.panel1_pos[1] += ball_speed
        elif self.key_state[2]:
            self.panel1_pos[1] -= ball_speed
        if self.key_state[1]:
            self.panel1_pos[0] -= ball_speed
        elif self.key_state[3]:
            self.panel1_pos[0] += ball_speed
        if self.key_state[4]:
            self.panel2_pos[1] += ball_speed
        elif self.key_state[6]:
            self.panel2_pos[1] -= ball_speed
        if self.key_state[5]:
            self.panel2_pos[0] += ball_speed
        elif self.key_state[7]:
            self.panel2_pos[0] -= ball_speed

    async def update(self):
        steps = 10
        for i in range(steps):
            movement = np.copy(self.ball_vec) * (0.4 / steps)
            self.ball_pos += movement

            collision_plane = self.check_collision_with_sides()
            if collision_plane:
                self.update_ball_vector(collision_plane)
                break
            await self.check_collision_with_goal_area()

        await self.send_callback(
            {
                "type": "state",
                "ball_pos": self.ball_pos.tolist(),
                "panel1": self.panel1_pos.tolist(),
                "panel2": self.panel2_pos.tolist(),
                "ball_rot": self.ball_rot.tolist(),
            }
        )

    # 벽4가지를 순회하며 어느 벽과 충돌했는지 판별하고 부딪힌 벽을 반환
    def check_collision_with_sides(self):
        for plane in self.planes:
            collision_point = self.get_collision_point_with_plane(plane)
            if isinstance(collision_point, np.ndarray):
                self.ball_pos = collision_point
                # 현재 공의 좌표에 평면의 법선벡터 * 2를 해서 더해준다
                self.ball_pos += plane[0] * 2
                return plane
        return None

    # 구가 평면과 부딪힌 좌표
    def get_collision_point_with_plane(self, plane):
        distance_to_plane = self.plane_distance_to_point(plane)
        if abs(distance_to_plane) <= 2:
            self.ball_rot -= plane[0] * 0.01  # 여기
            return self.ball_pos - (plane[0] * distance_to_plane)
        return None

    # 평면과 점 사이의 거리, 인자로 부딪힌 평면을 받고, 그 평면과 구의 중심사이의 거리를 계산한다
    def plane_distance_to_point(self, plane):
        # plane은 ((x, y, z), (원점으로부터의 거리)) -> 법선벡터, 원점으로부터의 거리로 구현
        a, b, c = plane[0]  # 법선벡터
        d = plane[1]  # 중심으로부터의 거리
        return abs(
            self.ball_pos[0] * a + self.ball_pos[1] * b + self.ball_pos[2] * c + d
        ) / math.sqrt(a**2 + b**2 + c**2)

    # panel이 위치한 평면과 충돌시
    async def check_collision_with_goal_area(self):
        if self.ball_pos[2] >= 48:  # z좌표가 48이상인경우 #player1쪽 벽과 충돌한경우
            if self.is_ball_in_panel(self.panel1_pos):  # x,y 좌표 판정
                self.handle_panel_collision(
                    self.panel1_plane, self.panel1_pos
                )  # panel1과 충돌한경우
            else:
                await self.player2_win()  # panel1이 위치한 면에 충돌한경우
        elif self.ball_pos[2] <= -48:
            if self.is_ball_in_panel(self.panel2_pos):
                self.handle_panel_collision(
                    self.panel2_plane, self.panel2_pos
                )  # panel2와 충돌한 경우
            else:
                await self.player1_win()

    # 공 중심의 x, y좌표가 panel안에 위치하는지 확인하는 함수
    def is_ball_in_panel(self, panel_pos):
        if abs(self.ball_pos[0] - panel_pos[0]) > 4:
            return False
        elif abs(self.ball_pos[1] - panel_pos[1]) > 4:
            return False
        return True

    # 판넬과 공이 충돌한 경우
    def handle_panel_collision(self, panel_plane, panel_pos):
        # 충돌지점 계산
        collision_point = self.get_collision_point_with_plane(panel_plane)
        # 충돌후 공의 좌표를 보정
        self.ball_pos = collision_point + panel_plane[0] * 2
        self.update_vector_by_panel(panel_plane, panel_pos)

    # 공 벡터 업데이트함수
    def update_ball_vector(self, collision_plane):
        dot_product = np.dot(self.ball_vec, collision_plane[0])
        reflection = collision_plane[0] * dot_product * 2
        self.ball_vec = self.ball_vec - reflection

    # 판넬과 공이 충돌한경우 ball_vec에 보정
    def update_vector_by_panel(self, panel_plane, panel_pos):
        self.update_ball_vector(panel_plane)
        self.update_ball_rotation(panel_plane)
        self.ball_vec[0] = (2 - (panel_pos[0] - self.ball_pos[0])) / 24
        self.ball_vec[1] = (2 - (panel_pos[1] - self.ball_pos[1])) / 24

    def update_ball_rotation(self, panel_plane):
        # 마찰력 또는 저항력을 나타내는 F 값을 적절히 계산
        F = self.ball_rot * -1

        # 충돌 모멘트(tau)를 계산
        tau = np.cross(panel_plane[0], F)

        # 관성 모멘트 텐서의 역행렬
        I_inv = np.diag([1 / (1.6)] * 3)

        # 각속도 변화량 계산
        delta_w = np.dot(I_inv, tau)

        # 기존의 회전 벡터에 변화를 더함
        self.ball_rot = self.ball_rot + delta_w

        # 회전 속도를 계산하고 너무 크면 조정
        spin_speed = np.linalg.norm(self.ball_rot)
        if spin_speed > 0.1:
            # 회전 벡터의 크기를 조정하여 안정적인 값 유지
            self.ball_rot *= 0.1 / spin_speed

    async def player1_win(self):
        self.ball_vec = np.array([0.0, 0.0, 1.0])
        self.angular_vec = np.array([0.0, 0.0, 0.0])
        self.ball_pos = np.array([0.0, 0.0, 0.0])
        self.player1_score += 1
        self.session_data['left_score'] += 1
        await self.send_score_callback()
        if self.player1_score >= GAME_END_SCORE:
            self.set_game_ended('left')

    async def player2_win(self):
        self.ball_vec = np.array([0.0, 0.0, 1.0])
        self.angular_vec = np.array([0.0, 0.0, 0.0])
        self.ball_pos = np.array([0.0, 0.0, 0.0])
        self.player2_score += 1
        self.session_data['right_score'] += 1
        await self.send_score_callback()
        if self.player2_score >= GAME_END_SCORE:
            self.set_game_ended('right')

    async def send_score_callback(self):
        await self.send_callback(
            {
                "type": "score",
                "left_score": self.player1_score,
                "right_score": self.player2_score,
                # DELETE!
                "scores": f"{self.player1_score}:{self.player2_score}",
            }
        )

    def set_game_ended(self, winner):
        self.game_state = "ended"
        game_round = self.session_data['game_round']
        players_name = self.session_data['username']
        if self.game_mode == "tournament":
            self.session_data['scores'] = '0:0'
            if game_round == 1 and winner == "left":
                self.win_history.append(players_name[0])
            elif game_round == 1 and winner == "right":
                self.win_history.append(players_name[1])
            elif game_round == 2 and winner == "left":
                self.win_history.append(players_name[2])
            elif game_round == 2 and winner == "right":
                self.win_history.append(players_name[3])
        self.session_data['game_round'] += 1
