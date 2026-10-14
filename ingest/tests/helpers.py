from model import Line, Page, Span


def make_line(text, y0=100.0, size=10.0, block=0, x0=50.0, flags=0, refs=()):
    bbox = (x0, y0, x0 + 6 * len(text), y0 + size * 1.2)
    return Line((Span(text, size, flags, bbox),), bbox, block, tuple(refs))


def make_page(number, lines, width=600.0, height=800.0):
    return Page(number, width, height, tuple(lines))


def line_with(page, text):
    return next(line for line in page.lines if text in line.text)


def page_of(pages, number):
    return next(page for page in pages if page.number == number)
