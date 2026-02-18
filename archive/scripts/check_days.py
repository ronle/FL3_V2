from datetime import date
for d in [date(2026,1,30), date(2026,1,31), date(2026,2,1), date(2026,2,2), date(2026,2,3)]:
    print(f"{d} = {d.strftime('%A')}")
