import re
for p in ("explain_pair.py", "check_frozen_case.py"):
    try:
        s = open(p, encoding="utf-8").read()
    except FileNotFoundError:
        print("bỏ qua (không có file):", p)
        continue
    pat = re.compile(r"^(?P<i>[ \t]*)from adapters\.cvat_api import load_task_tracks\n"
                     r"(?P=i)tracks, _ = load_task_tracks\(.*\)\n", re.M)
    new, n = pat.subn(lambda m: f"{m['i']}from cvat_loader import load_cvat\n{m['i']}tracks = load_cvat(args.path)\n", s)
    if n != 1:
        print(f"{p}: không tìm thấy đúng một chỗ để thay (tìm được {n}); hãy sửa tay hai dòng nhánh 'cvat'")
        continue
    open(p, "w", encoding="utf-8").write(new)
    print(f"{p}: đã thay nhánh cvat để dùng cvat_loader")
