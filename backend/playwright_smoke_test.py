from playwright.sync_api import sync_playwright

html = '''
<html>
  <body>
    <h1>LCI PDF Test</h1>
    <p>It works.</p>
  </body>
</html>
'''

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(html)
    pdf = page.pdf(format="Letter")
    with open("playwright_test.pdf", "wb") as f:
        f.write(pdf)
    browser.close()

print("OK: wrote playwright_test.pdf, bytes =", len(pdf))
