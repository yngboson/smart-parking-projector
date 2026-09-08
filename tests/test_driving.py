"""자전거 모델 · 경로 추종 · 후진 주차 검증.

여기서 확인하려는 것은 "차가 예쁘게 움직이는가"가 아니라
**유도선이 실제로 주행 가능한 선인가** 이다.

관제가 그린 선을 실제 차량이 최소 회전반경 안에서 따라갈 수 없다면 그 선은
현장에서 쓸모가 없다. 그래서 물리 제약(회전반경, 조향 각속도, 기어 변경 조건)을
먼저 강제하고, 그 위에서 추종과 주차를 검증한다.
"""

from __future__ import annotations

import math

import pytest

from sim.agents.driving import DrivingSkill, PathFollower
from sim.common.geometry import Pose, Vec2, angle_diff, wrap_angle
from sim.common.maneuver import plan_reverse_parking, slot_contains
from sim.common.vehicle import ControlInput, SelfState, VehicleSpec, footprint
from sim.world import physics
from sim.world.lot_builder import GridSpec, build_grid_lot

DT = 0.02


def rest(x=0.0, y=0.0, theta=0.0) -> SelfState:
    return SelfState(pose=Pose(x, y, theta), speed=0.0, steer=0.0, gear=1)


def drive(state, spec, cmd, seconds, dt=DT):
    n = int(round(seconds / dt))
    for _ in range(n):
        state = physics.step(state, spec, cmd, dt)
    return state


# ── 자전거 모델의 물리 제약 ────────────────────────────────────────


def test_min_turn_radius_is_respected() -> None:
    """최대 조향으로 돌아도 최소 회전반경보다 작게 못 돈다."""
    spec = VehicleSpec()
    state = rest()

    # 조향을 다 꺾고 한 바퀴 가까이 돌린다
    state = drive(state, spec, ControlInput(steer=spec.max_steer, accel=1.0, gear=1), 2.0)
    start = state.pose

    traced: list[Vec2] = []
    for _ in range(int(6.0 / DT)):
        state = physics.step(state, spec, ControlInput(spec.max_steer, 0.0, 1), DT)
        traced.append(state.pose.position)

    # 회전 중심은 뒷축에서 조향 반대쪽으로 R 만큼 떨어진 곳
    r = spec.min_turn_radius
    center = start.position + Vec2.from_angle(start.theta + math.pi / 2) * r
    radii = [p.distance_to(center) for p in traced]

    assert min(radii) > r * 0.97, f"최소 회전반경보다 작게 돌았습니다: {min(radii):.2f} < {r:.2f}"
    assert max(radii) < r * 1.03


def test_steering_cannot_jump() -> None:
    """핸들은 순간이동하지 않는다 — 조향 각속도 한계."""
    spec = VehicleSpec()
    state = rest()
    state = physics.step(state, spec, ControlInput(spec.max_steer, 1.0, 1), 0.05)
    assert state.steer <= spec.max_steer_rate * 0.05 + 1e-9


def test_cannot_shift_into_reverse_while_moving() -> None:
    """달리면서 후진 기어로 못 넣는다."""
    spec = VehicleSpec()
    state = drive(rest(), spec, ControlInput(0.0, 1.5, 1), 2.0)
    assert state.speed > 1.0

    moving = physics.step(state, spec, ControlInput(0.0, 0.0, -1), DT)
    assert moving.gear == 1, "주행 중에 기어가 바뀌었습니다"

    stopped = drive(state, spec, ControlInput(0.0, -spec.max_decel, 1), 3.0)
    assert stopped.speed == pytest.approx(0.0, abs=1e-6)
    shifted = physics.step(stopped, spec, ControlInput(0.0, 0.0, -1), DT)
    assert shifted.gear == -1


def test_reverse_moves_backwards() -> None:
    spec = VehicleSpec()
    state = SelfState(Pose(0.0, 0.0, 0.0), 0.0, 0.0, -1)
    state = drive(state, spec, ControlInput(0.0, 0.6, -1), 2.0)
    assert state.pose.x < -0.3, "후진 기어인데 앞으로 갔습니다"
    assert state.pose.y == pytest.approx(0.0, abs=1e-9)


def test_speed_is_capped_per_gear() -> None:
    spec = VehicleSpec()
    fwd = drive(rest(), spec, ControlInput(0.0, spec.max_accel, 1), 20.0)
    assert fwd.speed == pytest.approx(spec.max_speed, abs=1e-6)

    rev = drive(SelfState(Pose(0, 0, 0), 0.0, 0.0, -1), spec,
                ControlInput(0.0, spec.max_accel, -1), 20.0)
    assert rev.speed == pytest.approx(spec.max_reverse_speed, abs=1e-6)


# ── Pure Pursuit 경로 추종 ─────────────────────────────────────────


def follow(follower: PathFollower, state: SelfState, spec: VehicleSpec,
           seconds: float = 40.0, dt: float = DT):
    """경로 끝까지 따라가며 궤적을 기록한다."""
    trace = [state]
    for _ in range(int(seconds / dt)):
        state = physics.step(state, spec, follower.control(state), dt)
        trace.append(state)
        if follower.is_finished(state):
            break
    return state, trace


def test_follower_converges_onto_a_straight_line() -> None:
    """옆으로 벗어난 상태에서 시작해도 선 위로 수렴해야 한다."""
    spec = VehicleSpec()
    path = [Vec2(x, 0.0) for x in range(0, 61, 2)]

    f = PathFollower(spec)
    f.set_path(path)
    state = rest(0.0, 3.0, 0.0)          # 3m 옆에서 출발

    final, trace = follow(f, state, spec, seconds=45.0)

    tail = trace[len(trace) // 2:]
    worst = max(abs(s.pose.y) for s in tail)
    assert worst < 0.25, f"선 위로 수렴하지 못했습니다 (최대 편차 {worst:.2f} m)"


def test_follower_takes_a_right_angle_corner_without_breaking_physics() -> None:
    """직각 코너를 실제 회전반경 안에서 돌아야 한다.

    통로 그래프가 만드는 경로는 직각이다. 이걸 못 돌면 유도선이 무의미하다.
    """
    spec = VehicleSpec()
    path = [Vec2(x, 0.0) for x in range(0, 31, 2)] + [Vec2(30.0, y) for y in range(2, 31, 2)]

    f = PathFollower(spec)
    f.set_path(path)
    state, trace = follow(f, rest(0.0, 0.0, 0.0), spec, seconds=60.0)

    # 조향각이 물리 한계를 넘지 않았는지
    assert all(abs(s.steer) <= spec.max_steer + 1e-9 for s in trace)

    # 코너를 실제로 돌아 북쪽을 향했는지
    assert state.pose.y > 24.0, f"코너를 다 돌지 못했습니다 (y={state.pose.y:.1f})"
    assert abs(angle_diff(state.pose.theta, math.pi / 2)) < 0.2

    # 코너 바깥으로 크게 밀려나지 않았는지 (통로 폭 6m 안에 있어야 한다)
    overshoot = max(s.pose.x for s in trace) - 30.0
    assert overshoot < 3.0, f"코너에서 {overshoot:.1f} m 밀려났습니다 — 통로를 벗어납니다"


def test_follower_stops_at_the_end_of_the_path() -> None:
    spec = VehicleSpec()
    path = [Vec2(x, 0.0) for x in range(0, 41, 2)]
    f = PathFollower(spec)
    f.set_path(path)

    final, _ = follow(f, rest(), spec, seconds=45.0)
    assert final.speed < 0.1
    assert final.pose.x == pytest.approx(40.0, abs=0.6)


# ── 후진 주차 ──────────────────────────────────────────────────────


def test_reverse_parking_geometry_stages_past_the_slot() -> None:
    """정차 지점은 주차면을 지나친 곳이어야 한다. 그래야 후진으로 들어갈 수 있다."""
    spec = VehicleSpec()
    slot_center = Vec2(20.0, 12.0)
    slot_heading = math.pi / 2          # 앞머리가 북쪽(통로 쪽)
    approach = 0.0                       # 통로는 동쪽으로 흐른다

    m = plan_reverse_parking(
        slot_center, slot_heading, approach,
        spec.rear_axle_to_center, spec.min_turn_radius * 1.15,
    )

    assert m.staging.x > slot_center.x, "정차 지점이 주차면을 지나치지 않았습니다"
    assert abs(angle_diff(m.staging.theta, approach)) < 1e-6
    assert m.reverse_path[0].distance_to(m.staging.position) < 1e-9
    assert m.reverse_path[-1].distance_to(m.final.position) < 1e-9


@pytest.mark.parametrize("slot_heading", [math.pi / 2, -math.pi / 2])
@pytest.mark.parametrize("approach", [0.0, math.pi])
def test_car_reverses_into_the_slot(slot_heading: float, approach: float) -> None:
    """정차 지점에서 후진해 주차면 안에 정확히 들어가야 한다.

    통로가 동쪽으로 흐르든 서쪽으로 흐르든, 주차면이 통로 위쪽이든 아래쪽이든
    같은 절차로 들어갈 수 있어야 한다 — 실제 도면에 네 조합이 모두 나온다.
    """
    spec = VehicleSpec()
    slot_center = Vec2(30.0, 20.0)
    slot_len, slot_w = 5.0, 2.5

    m = plan_reverse_parking(
        slot_center, slot_heading, approach,
        spec.rear_axle_to_center, spec.min_turn_radius * 1.15,
    )

    f = PathFollower(spec, DrivingSkill())
    f.set_path(m.reverse_path, gear=-1)

    state = SelfState(pose=m.staging, speed=0.0, steer=0.0, gear=-1)
    state, _ = follow(f, state, spec, seconds=60.0)

    # 최종 자세
    pos_err = state.pose.position.distance_to(m.final.position)
    ang_err = abs(angle_diff(state.pose.theta, m.final.theta))
    assert pos_err < 0.35, f"주차 위치 오차 {pos_err:.2f} m"
    assert ang_err < 0.12, f"주차 각도 오차 {math.degrees(ang_err):.1f}°"

    # 차체가 주차면 안에 들어갔는가 — 실제로 중요한 것은 이쪽이다
    corners = footprint(state.pose, spec)
    assert slot_contains(corners, slot_center, slot_heading, slot_len, slot_w, margin=0.25), (
        "차량이 주차면 밖으로 삐져나왔습니다"
    )


def test_reverse_parking_works_on_the_real_layout() -> None:
    """실제 생성한 도면의 주차면들에 대해 후진 주차가 성립하는지.

    구역마다 통로 방향과 주차면 방향의 조합이 다르므로 대표 주차면을 모두 시험한다.
    """
    spec = VehicleSpec()
    lot = build_grid_lot(GridSpec())

    aisle_heading = {a.id: a.heading for a in lot.aisles if a.axis == "h"}

    failures: list[str] = []
    for row in lot.rows:
        slot = lot.slots_in_row(row)[9]           # 각 구역 중간쯤
        node = lot.nodes[slot.access_node]
        approach = aisle_heading[node.aisle]

        m = plan_reverse_parking(
            slot.center, slot.heading, approach,
            spec.rear_axle_to_center, spec.min_turn_radius * 1.15,
        )
        f = PathFollower(spec, DrivingSkill())
        f.set_path(m.reverse_path, gear=-1)

        state = SelfState(pose=m.staging, speed=0.0, steer=0.0, gear=-1)
        state, _ = follow(f, state, spec, seconds=60.0)

        corners = footprint(state.pose, spec)
        if not slot_contains(corners, slot.center, slot.heading, slot.length, slot.width, 0.25):
            failures.append(
                f"{slot.id} (통로 {node.aisle}, 진입 {math.degrees(approach):.0f}°)"
            )

    assert not failures, "후진 주차에 실패한 주차면: " + ", ".join(failures)


def test_parked_car_faces_the_aisle() -> None:
    """후진 주차이므로 주차를 마친 차의 앞머리는 통로를 향해야 한다."""
    spec = VehicleSpec()
    lot = build_grid_lot(GridSpec())
    slot = lot.slots_in_row("C")[3]

    m = plan_reverse_parking(
        slot.center, slot.heading, 0.0,
        spec.rear_axle_to_center, spec.min_turn_radius * 1.15,
    )
    node_pos = lot.node_pos(slot.access_node)
    to_aisle = (node_pos - slot.center).normalized()
    facing = Vec2.from_angle(m.final.theta)
    assert to_aisle.dot(facing) > 0.99


def test_slot_contains_rejects_a_car_sticking_out() -> None:
    """주차면 판정이 실제로 거짓을 걸러내는지 — 통과만 하는 검사는 의미가 없다."""
    spec = VehicleSpec()
    center = Vec2(0.0, 0.0)
    heading = math.pi / 2

    good = footprint(Pose(0.0, -spec.rear_axle_to_center, heading), spec)
    assert slot_contains(good, center, heading, 5.0, 2.5, margin=0.25)

    # 옆으로 1m 밀린 차
    bad = footprint(Pose(1.0, -spec.rear_axle_to_center, heading), spec)
    assert not slot_contains(bad, center, heading, 5.0, 2.5, margin=0.25)

    # 45° 비뚤어진 차
    skew = footprint(Pose(0.0, -spec.rear_axle_to_center, heading + 0.8), spec)
    assert not slot_contains(skew, center, heading, 5.0, 2.5, margin=0.25)
