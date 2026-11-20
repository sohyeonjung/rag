"""Wholly fictional companies and financials. Never represent live API evidence."""
NAMES = ['가온반도체', '나래전자', '다온소재', '라온테크', '마루반도체', '바른전자',
         '새봄소재', '아람테크', '자람반도체', '차오름전자', '큰별소재', '한빛테크']
GROWTH = [-12, 18, 7, 24, 12, -5, 4, 30, 22, 15, 2, 11]

def companies():
    return [dict(stock_code=f'D{i:05}', corp_code=f'DEMO{i:04}', name=n,
                 summary='가상 반도체·전자 소재 기업. 데모 검증용입니다.', wiki_title=n,
                 demo=True) for i,n in enumerate(NAMES)]

def financial(code, year):
    i = int(code[1:])
    return dict(stock_code=code, year=year, revenue=100_000_000*(100+GROWTH[i]),
                previous_revenue=10_000_000_000, operating_profit=(-1 if i in (2,6) else 1)*1_000_000_000,
                basis='CFS', currency='KRW', source_url='', source='가상 재무 데이터', demo=True)
