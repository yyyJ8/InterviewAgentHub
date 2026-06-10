"""文件解析管道测试"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path，确保模块可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_parse_txt():
    from tools import parse_file

    text = parse_file(FIXTURES_DIR / "sample_jd.txt")
    assert "高级 Python 后端工程师" in text
    assert "Python" in text
    assert "Django" in text
    assert "MySQL" in text
    assert len(text) > 100
    print("  [OK] test_parse_txt 通过")


def test_parse_resume():
    from tools import parse_file

    text = parse_file(FIXTURES_DIR / "sample_resume.txt")
    assert "张三" in text
    assert "电商平台核心系统" in text
    assert "Python" in text
    assert "Django" in text
    print("  [OK] test_parse_resume 通过")


def test_clean_text():
    from tools.text_cleaner import clean_text

    # 多余空白
    assert clean_text("  a  b  ") == "a b"
    # 连续空行压缩
    assert "a\n\nb" in clean_text("a\n\n\n\n\nb")
    print("  [OK] test_clean_text 通过")


def test_unsupported_format():
    from tools import parse_file

    try:
        parse_file("test.xyz")
        assert False
    except ValueError as e:
        assert "不支持" in str(e)
    print("  [OK] test_unsupported_format 通过")


def test_file_not_found():
    from tools import parse_file

    try:
        parse_file("not_exists.pdf")
        assert False
    except FileNotFoundError:
        pass
    print("  [OK] test_file_not_found 通过")


if __name__ == "__main__":
    print("文件解析管道测试\n" + "=" * 20)
    test_clean_text()
    test_parse_txt()
    test_parse_resume()
    test_unsupported_format()
    test_file_not_found()
    print("\n[OK] 全部通过")
