"""보내는 메세지와 프린터 필드이름의 약속.

사용자는 필드이름을 입력하지 않는다. 프린터 이미지(라벨)의 입력 필드를 text1, text2, text3 …
순서로 만들어 두기로 약속하고, 프로그램은 메세지를 추가한 순서대로 text1, text2 … 를 붙여 보낸다.
"""
PREFIX = "text"


def field_name(n):
    """n번째(1부터) 메세지의 프린터 필드이름."""
    return "%s%d" % (PREFIX, n)


def build_fields(messages):
    """메세지 목록 -> [(text1, 메세지1), (text2, 메세지2), ...]"""
    return [(field_name(i + 1), m) for i, m in enumerate(messages)]


def friendly(name):
    """화면에는 필드이름 대신 '메세지 N'으로 보여 준다(약속 밖의 이름은 그대로)."""
    if name.startswith(PREFIX) and name[len(PREFIX):].isdigit():
        return "메세지 %s" % name[len(PREFIX):]
    return name
