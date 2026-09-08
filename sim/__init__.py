"""빔 프로젝터 주차 유도 시스템 개념검증 시뮬레이터.

계층 구조는 CLAUDE.md 를 읽을 것. 요약:
    common  — 세 계층이 공유하는 불변 값 타입만
    world   — 주차장 환경, 물리, 센서, 프로젝터 (배선 담당)
    control — 관제 알고리즘 (common 만 import 가능)
    agents  — 개별 차량 에이전트 (common 만 import 가능)
"""
