#!/usr/bin/env python3
"""
策略列表功能实际点击测试

使用 Playwright 进行浏览器自动化测试
"""
from playwright.sync_api import sync_playwright, expect
import time

def test_portfolio_strategy_list():
    """策略列表功能完整点击测试"""

    print("\n=== 策略列表功能实际点击测试 ===\n")

    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        # 1. 页面加载测试
        print("【1. 页面加载测试】")
        page.goto('http://localhost:8001', timeout=10000)
        page.wait_for_load_state('networkidle')
        print(f"   ✅ 页面加载成功 (URL: {page.url})")

        # 2. Tab 切换测试
        print("\n【2. Tab 切换测试】")
        portfolio_tab = page.locator('[data-tab="portfolio-list"]')
        portfolio_tab.click()
        time.sleep(1)

        # 验证策略列表面板显示
        strategy_panel = page.locator('#panel-portfolio-list')
        expect(strategy_panel).to_be_visible()
        print('   ✅ 点击"组合回测"Tab，策略列表面板显示')

        # 3. 策略列表测试
        print("\n【3. 策略列表测试】")
        strategy_list = page.locator('#strategy-list')
        expect(strategy_list).to_be_visible()
        print("   ✅ 策略列表容器可见")

        # 验证策略卡片数量
        strategy_cards = page.locator('.strategy-item')
        count = strategy_cards.count()
        print(f"   ✅ 策略卡片数量：{count}个")

        # 4. 筛选功能测试
        print("\n【4. 筛选功能测试】")
        filter_buttons = page.locator('.filter-btn')
        filter_count = filter_buttons.count()
        print(f"   ✅ 筛选按钮数量：{filter_count}个")

        # 点击全部按钮
        all_filter = page.locator('[data-filter="all"]')
        all_filter.click()
        time.sleep(0.5)
        print("   ✅ 点击全部筛选按钮")

        # 5. 策略详情联动测试
        print("\n【5. 策略详情联动测试】")
        if count > 0:
            # 点击第一个策略
            first_strategy = strategy_cards.first
            first_strategy.click()
            time.sleep(1)

            # 验证详情面板显示
            detail_content = page.locator('#detail-content')
            expect(detail_content).to_be_visible()
            print("   ✅ 点击策略，详情面板显示")

            # 验证策略名称显示
            strategy_name = page.locator('#detail-name')
            name_text = strategy_name.inner_text()
            print(f"   ✅ 详情显示策略名称：{name_text}")

            # 6. 详情 Tab 切换测试
            print("\n【6. 详情 Tab 切换测试】")
            detail_tabs = page.locator('.detail-tab')
            tab_count = detail_tabs.count()
            print(f"   ✅ 详情 Tab 数量：{tab_count}个")

            # 点击策略逻辑Tab
            logic_tab = page.locator('[data-tab="logic"]')
            logic_tab.click()
            time.sleep(0.5)
            logic_content = page.locator('#tab-logic')
            expect(logic_content).to_be_visible()
            print("   ✅ 点击策略逻辑Tab，内容显示")

            # 点击参数说明Tab
            params_tab = page.locator('[data-tab="params"]')
            params_tab.click()
            time.sleep(0.5)
            params_content = page.locator('#tab-params')
            expect(params_content).to_be_visible()
            print("   ✅ 点击参数说明Tab，内容显示")

            # 点击历史表现Tab
            perf_tab = page.locator('[data-tab="performance"]')
            perf_tab.click()
            time.sleep(0.5)
            perf_content = page.locator('#tab-performance')
            expect(perf_content).to_be_visible()
            print("   ✅ 点击历史表现Tab，内容显示")
        else:
            print("   ⚠️ 无策略卡片，跳过详情测试")

        # 7. 截图保存
        print("\n【7. 截图验证】")
        screenshot_path = '/opt/quantitative-trading/tests/screenshots/portfolio_strategy_test.png'
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"   ✅ 截图已保存：{screenshot_path}")

        # 关闭浏览器
        browser.close()

        print("\n=== 测试完成 ===\n")
        print("✅ 所有点击测试通过！")

        return True

if __name__ == '__main__':
    import os

    # 创建截图目录
    os.makedirs('/opt/quantitative-trading/tests/screenshots', exist_ok=True)

    try:
        test_portfolio_strategy_list()
    except Exception as e:
        print(f"\n❌ 测试失败：{e}\n")
        raise
