@echo off
set DATABASE_URL=postgresql://fr3_app:cviHs9NaUqS45%%240gjkBu2znKyFV%%21%%40LCTOQd18RDW@localhost:5432/fl3
python "%~dp0scripts\create_signal_evaluations_table.py"
pause
