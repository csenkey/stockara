#!/usr/bin/env python3
"""Generate a PDF document about the Stock Monitoring System."""

import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
    PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

# Colors
PRIMARY = HexColor('#2563EB')
SECONDARY = HexColor('#1E40AF')
ACCENT = HexColor('#10B981')
LIGHT_BG = HexColor('#F8FAFC')
DARK_TEXT = HexColor('#1E293B')
GRAY_TEXT = HexColor('#64748B')

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "Stock_Monitoring_System.pdf")


def create_architecture_diagram():
    """Create the system architecture diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(10, 7))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 7)
    ax.axis('off')
    ax.set_facecolor('#F8FAFC')
    fig.patch.set_facecolor('#F8FAFC')

    # Title
    ax.text(5, 6.7, 'System Architecture', ha='center', va='top',
            fontsize=16, fontweight='bold', color='#1E293B')

    # AWS Cloud box
    cloud = FancyBboxPatch((0.3, 0.3), 9.4, 6.0, boxstyle="round,pad=0.1",
                           facecolor='#EFF6FF', edgecolor='#2563EB', linewidth=2)
    ax.add_patch(cloud)
    ax.text(5, 6.1, 'AWS Cloud', ha='center', fontsize=11, color='#2563EB', fontweight='bold')

    # Components
    components = [
        (1.5, 5.0, 'CloudFront\n+ S3', '#DBEAFE', '#2563EB'),
        (4.0, 5.0, 'API Gateway', '#DBEAFE', '#2563EB'),
        (7.0, 5.0, 'EventBridge\nScheduler', '#FEF3C7', '#D97706'),
        (1.5, 3.2, 'Lambda:\nAPI Handler', '#D1FAE5', '#059669'),
        (4.0, 3.2, 'Lambda:\nStock Collector', '#D1FAE5', '#059669'),
        (6.5, 3.2, 'Lambda:\nNews Collector', '#D1FAE5', '#059669'),
        (9.0, 3.2, 'Lambda:\nAI Analyzer', '#D1FAE5', '#059669'),
        (3.0, 1.2, 'RDS PostgreSQL\n(Serverless v2)', '#FEE2E2', '#DC2626'),
        (6.5, 1.2, 'Cognito\n(Auth)', '#E0E7FF', '#4F46E5'),
        (9.0, 1.2, 'CloudWatch\n(Monitoring)', '#FEF3C7', '#D97706'),
    ]

    for x, y, label, facecolor, edgecolor in components:
        box = FancyBboxPatch((x-0.7, y-0.4), 1.4, 0.8,
                             boxstyle="round,pad=0.05",
                             facecolor=facecolor, edgecolor=edgecolor, linewidth=1.5)
        ax.add_patch(box)
        ax.text(x, y, label, ha='center', va='center', fontsize=7,
                color='#1E293B', fontweight='medium')

    # Arrows
    arrows = [
        (1.5, 4.6, 0, -0.8), (4.0, 4.6, 0, -0.8),
        (7.0, 4.6, -0.8, -0.8), (7.0, 4.6, 0.5, -0.8), (7.0, 4.6, 2.0, -0.8),
        (1.5, 2.8, 1.0, -1.0), (4.0, 2.8, -0.5, -1.0), (6.5, 2.8, -1.5, -1.0),
    ]
    for x, y, dx, dy in arrows:
        ax.annotate('', xy=(x+dx, y+dy), xytext=(x, y),
                    arrowprops=dict(arrowstyle='->', color='#64748B', lw=1.2))

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def create_cost_chart():
    """Create a cost breakdown pie chart."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    fig.patch.set_facecolor('#F8FAFC')

    # Standard config
    labels1 = ['RDS Serverless', 'OpenAI API', 'CloudWatch', 'S3+CloudFront', 'KMS', 'API Gateway']
    sizes1 = [43.80, 20.0, 4.0, 1.5, 1.0, 0.35]
    colors1 = ['#DC2626', '#8B5CF6', '#F59E0B', '#2563EB', '#10B981', '#6366F1']
    explode1 = (0.05, 0.02, 0, 0, 0, 0)

    ax1.pie(sizes1, explode=explode1, labels=labels1, colors=colors1,
            autopct='$%.0f', shadow=False, startangle=90,
            textprops={'fontsize': 8})
    ax1.set_title('Standard Config\n~$71/month', fontsize=11, fontweight='bold', color='#1E293B')

    # Budget config
    labels2 = ['OpenAI API', 'CloudWatch', 'S3+CloudFront', 'KMS', 'API Gateway']
    sizes2 = [20.0, 2.5, 1.5, 1.0, 0.35]
    colors2 = ['#8B5CF6', '#F59E0B', '#2563EB', '#10B981', '#6366F1']
    explode2 = (0.05, 0, 0, 0, 0)

    ax2.pie(sizes2, explode=explode2, labels=labels2, colors=colors2,
            autopct='$%.0f', shadow=False, startangle=90,
            textprops={'fontsize': 8})
    ax2.set_title('Budget Config (Neon DB)\n~$25/month', fontsize=11, fontweight='bold', color='#1E293B')

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def create_data_flow_diagram():
    """Create the daily data flow diagram."""
    fig, ax = plt.subplots(1, 1, figsize=(10, 3.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.5)
    ax.axis('off')
    ax.set_facecolor('#F8FAFC')
    fig.patch.set_facecolor('#F8FAFC')

    ax.text(5, 3.3, 'Daily Data Pipeline', ha='center', fontsize=14,
            fontweight='bold', color='#1E293B')

    steps = [
        (1.0, 1.8, '21:00 UTC\nStock\nCollector', '#DBEAFE', '#2563EB'),
        (3.2, 1.8, 'Every 15min\nNews\nCollector', '#FEF3C7', '#D97706'),
        (5.4, 1.8, '22:00 UTC\nAI\nAnalyzer', '#D1FAE5', '#059669'),
        (7.6, 1.8, 'On Request\nSuggestion\nEngine', '#E0E7FF', '#4F46E5'),
        (9.5, 1.8, 'User\nDashboard', '#FCE7F3', '#DB2777'),
    ]

    for x, y, label, fc, ec in steps:
        box = FancyBboxPatch((x-0.65, y-0.55), 1.3, 1.1,
                             boxstyle="round,pad=0.08",
                             facecolor=fc, edgecolor=ec, linewidth=1.5)
        ax.add_patch(box)
        ax.text(x, y, label, ha='center', va='center', fontsize=7.5,
                color='#1E293B', fontweight='medium')

    # Arrows between steps
    for i in range(len(steps)-1):
        x1 = steps[i][0] + 0.7
        x2 = steps[i+1][0] - 0.7
        ax.annotate('', xy=(x2, 1.8), xytext=(x1, 1.8),
                    arrowprops=dict(arrowstyle='->', color='#64748B', lw=2))

    # Data sources below
    sources = [
        (1.0, 0.5, 'yfinance\nAlpha Vantage'),
        (3.2, 0.5, 'NewsAPI\nFinnhub'),
        (5.4, 0.5, 'OpenAI\nGPT-4o-mini'),
    ]
    for x, y, label in sources:
        ax.text(x, y, label, ha='center', va='center', fontsize=7,
                color='#64748B', style='italic')
        ax.annotate('', xy=(x, 1.2), xytext=(x, 0.8),
                    arrowprops=dict(arrowstyle='->', color='#94A3B8', lw=1))

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def create_tech_stack_chart():
    """Create a technology stack visual."""
    fig, ax = plt.subplots(1, 1, figsize=(10, 3))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis('off')
    ax.set_facecolor('#F8FAFC')
    fig.patch.set_facecolor('#F8FAFC')

    ax.text(5, 2.8, 'Technology Stack', ha='center', fontsize=14,
            fontweight='bold', color='#1E293B')

    categories = [
        (1.5, 'Backend', ['Python 3.12', 'FastAPI', 'Mangum', 'pandas'], '#D1FAE5', '#059669'),
        (4.0, 'Frontend', ['React 18', 'TypeScript', 'Vite', 'Tailwind'], '#DBEAFE', '#2563EB'),
        (6.5, 'Data', ['PostgreSQL', 'yfinance', 'OpenAI', 'NewsAPI'], '#FEE2E2', '#DC2626'),
        (9.0, 'Cloud', ['AWS Lambda', 'API Gateway', 'CDK', 'CloudWatch'], '#FEF3C7', '#D97706'),
    ]

    for x, title, items, fc, ec in categories:
        box = FancyBboxPatch((x-1.1, 0.2), 2.2, 2.3,
                             boxstyle="round,pad=0.08",
                             facecolor=fc, edgecolor=ec, linewidth=1.5)
        ax.add_patch(box)
        ax.text(x, 2.2, title, ha='center', va='center', fontsize=9,
                fontweight='bold', color=ec)
        for i, item in enumerate(items):
            ax.text(x, 1.7 - i*0.4, item, ha='center', va='center',
                    fontsize=8, color='#374151')

    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return buf


def build_pdf():
    """Build the complete PDF document."""
    doc = SimpleDocTemplate(
        OUTPUT_FILE, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='Title2', parent=styles['Title'],
        fontSize=24, textColor=PRIMARY, spaceAfter=20
    ))
    styles.add(ParagraphStyle(
        name='Heading2Custom', parent=styles['Heading2'],
        fontSize=16, textColor=SECONDARY, spaceBefore=20, spaceAfter=10
    ))
    styles.add(ParagraphStyle(
        name='BodyCustom', parent=styles['Normal'],
        fontSize=10, textColor=DARK_TEXT, spaceAfter=8, leading=14
    ))
    styles.add(ParagraphStyle(
        name='Subtitle', parent=styles['Normal'],
        fontSize=12, textColor=GRAY_TEXT, spaceAfter=30, alignment=TA_CENTER
    ))

    story = []

    # ===== TITLE PAGE =====
    story.append(Spacer(1, 4*cm))
    story.append(Paragraph("Stock Monitoring &<br/>Analysis System", styles['Title2']))
    story.append(Paragraph(
        "A serverless, AI-powered stock analysis platform on AWS",
        styles['Subtitle']
    ))
    story.append(Spacer(1, 1*cm))

    # Tech stack diagram
    tech_buf = create_tech_stack_chart()
    story.append(Image(tech_buf, width=16*cm, height=4.8*cm))
    story.append(Spacer(1, 2*cm))

    story.append(Paragraph(
        "Designed for 10-100 users • Minimal hosting cost • Daily AI recommendations",
        styles['Subtitle']
    ))
    story.append(PageBreak())

    # ===== OVERVIEW PAGE =====
    story.append(Paragraph("System Overview", styles['Heading2Custom']))
    story.append(Paragraph(
        "The Stock Monitoring and Analysis System is a cloud-hosted web application that "
        "collects daily stock market data and news, applies AI-driven analysis to generate "
        "buy/hold/sell recommendations, and provides personalized portfolio suggestions "
        "through a web dashboard.",
        styles['BodyCustom']
    ))
    story.append(Spacer(1, 5*mm))

    features = [
        "<b>1000+ stocks monitored</b> — categorized by sector and company size",
        "<b>Daily AI analysis</b> — GPT-4o-mini generates BUY/HOLD/SELL recommendations",
        "<b>Encrypted portfolios</b> — AES-256-GCM via AWS KMS",
        "<b>Personalized suggestions</b> — filtered by sector, size, and risk preferences",
        "<b>Serverless architecture</b> — scales to zero, minimal cost at low usage",
        "<b>Automated pipeline</b> — EventBridge schedules for daily data collection",
    ]
    for f in features:
        story.append(Paragraph(f"• {f}", styles['BodyCustom']))

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("Architecture", styles['Heading2Custom']))

    arch_buf = create_architecture_diagram()
    story.append(Image(arch_buf, width=16*cm, height=11.2*cm))
    story.append(PageBreak())

    # ===== DATA FLOW PAGE =====
    story.append(Paragraph("Daily Data Pipeline", styles['Heading2Custom']))
    story.append(Paragraph(
        "The system operates on a daily schedule, collecting data after market close "
        "and generating AI analysis within hours. News is collected continuously every "
        "15 minutes for real-time market sentiment.",
        styles['BodyCustom']
    ))
    story.append(Spacer(1, 5*mm))

    flow_buf = create_data_flow_diagram()
    story.append(Image(flow_buf, width=16*cm, height=5.6*cm))
    story.append(Spacer(1, 1*cm))

    # Pipeline details table
    pipeline_data = [
        ['Component', 'Schedule', 'Description'],
        ['Stock Collector', '21:00 UTC daily', 'Fetches OHLCV data for 1000+ tickers via yfinance'],
        ['News Collector', 'Every 15 minutes', 'Polls NewsAPI + Finnhub, generates AI summaries'],
        ['AI Analyzer', '22:00 UTC daily', 'Calculates SMA/RSI/MACD, calls GPT-4o-mini'],
        ['Suggestion Engine', 'On user request', 'Compares portfolio vs analysis, filters by prefs'],
    ]
    t = Table(pipeline_data, colWidths=[3.5*cm, 3.5*cm, 9.5*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LIGHT_BG]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(PageBreak())

    # ===== COST PAGE =====
    story.append(Paragraph("Cost Estimation", styles['Heading2Custom']))
    story.append(Paragraph(
        "Two deployment configurations are available depending on budget constraints. "
        "The budget configuration uses Neon's free-tier PostgreSQL to eliminate the "
        "largest cost (RDS), bringing total monthly spend under $30.",
        styles['BodyCustom']
    ))
    story.append(Spacer(1, 5*mm))

    cost_buf = create_cost_chart()
    story.append(Image(cost_buf, width=16*cm, height=6.4*cm))
    story.append(Spacer(1, 1*cm))

    # Cost table - Standard
    story.append(Paragraph("<b>Standard Configuration</b>", styles['BodyCustom']))
    cost_data_std = [
        ['Service', 'Usage', 'Monthly Cost'],
        ['Lambda', '~50K invocations/month', '$0 (free tier)'],
        ['API Gateway', '~100K requests/month', '$0.35'],
        ['RDS Serverless v2', '0.5 ACU minimum', '$43.80'],
        ['S3 + CloudFront', 'Frontend hosting', '$1–2'],
        ['Cognito', '<50K users', '$0 (free tier)'],
        ['CloudWatch', 'Logs + metrics + alarms', '$3–5'],
        ['OpenAI API (GPT-4o-mini)', '~1000 stocks × 30 days', '$15–25'],
        ['KMS', '1 key + decrypt calls', '$1'],
        ['', '', ''],
        ['TOTAL', '', '$65–77/month'],
    ]
    t2 = Table(cost_data_std, colWidths=[5*cm, 5*cm, 3.5*cm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [white, LIGHT_BG]),
        ('BACKGROUND', (0, -1), (-1, -1), HexColor('#EFF6FF')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t2)
    story.append(Spacer(1, 8*mm))

    # Cost table - Budget
    story.append(Paragraph("<b>Budget Configuration (≤$30/month)</b>", styles['BodyCustom']))
    cost_data_budget = [
        ['Service', 'Monthly Cost'],
        ['Lambda + API Gateway', '$0.35'],
        ['Neon PostgreSQL (free tier)', '$0'],
        ['S3 + CloudFront', '$1–2'],
        ['Cognito', '$0'],
        ['CloudWatch (basic)', '$2–3'],
        ['OpenAI API (GPT-4o-mini)', '$15–25'],
        ['KMS', '$1'],
        ['', ''],
        ['TOTAL', '$20–30/month'],
    ]
    t3 = Table(cost_data_budget, colWidths=[7*cm, 3.5*cm])
    t3.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), ACCENT),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -2), [white, LIGHT_BG]),
        ('BACKGROUND', (0, -1), (-1, -1), HexColor('#ECFDF5')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t3)
    story.append(PageBreak())

    # ===== SECURITY & DESIGN PAGE =====
    story.append(Paragraph("Security & Design Decisions", styles['Heading2Custom']))

    security_items = [
        "<b>Encryption at rest</b> — RDS storage encryption, S3 bucket encryption, "
        "portfolio data encrypted with AES-256-GCM via AWS KMS",
        "<b>Encryption in transit</b> — All traffic over HTTPS (CloudFront + API Gateway)",
        "<b>Authentication</b> — AWS Cognito with JWT tokens, password policy "
        "(8+ chars, uppercase, lowercase, digit), account lockout after 5 failed attempts",
        "<b>Authorization</b> — Cognito authorizer on API Gateway, user-scoped portfolio access",
        "<b>Least privilege</b> — Separate IAM roles per Lambda with minimal permissions",
        "<b>Secrets management</b> — API keys via AWS Secrets Manager, never in code",
        "<b>Session security</b> — 30-minute inactivity timeout, token expiry enforcement",
    ]
    for item in security_items:
        story.append(Paragraph(f"• {item}", styles['BodyCustom']))

    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("Database Schema", styles['Heading2Custom']))
    story.append(Paragraph(
        "PostgreSQL with 7 tables designed for efficient time-series queries "
        "and encrypted portfolio storage:",
        styles['BodyCustom']
    ))

    schema_data = [
        ['Table', 'Purpose', 'Key Fields'],
        ['stocks', 'Watchlist (1000+ tickers)', 'ticker PK, sector, company_size'],
        ['stock_data', 'Daily OHLCV prices', 'ticker, trading_date, OHLCV, UNIQUE'],
        ['news_summaries', 'AI-summarized articles', 'title, tickers[], summary (≤500)'],
        ['analysis_results', 'Daily recommendations', 'BUY/HOLD/SELL, risk, confidence'],
        ['users', 'Cognito user mirror', 'UUID id, email'],
        ['portfolios', 'Encrypted holdings', 'encrypted_data TEXT (AES-256)'],
        ['user_preferences', 'Filter settings', 'sectors[], sizes[], max_risk'],
    ]
    t4 = Table(schema_data, colWidths=[3.2*cm, 4.5*cm, 6.8*cm])
    t4.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), SECONDARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LIGHT_BG]),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t4)
    story.append(Spacer(1, 1*cm))

    story.append(Paragraph("API Endpoints", styles['Heading2Custom']))
    api_data = [
        ['Method', 'Path', 'Description'],
        ['POST', '/api/auth/register', 'Register new user'],
        ['POST', '/api/auth/login', 'Login, returns JWT'],
        ['GET', '/api/portfolio', 'Decrypted user portfolio'],
        ['PUT', '/api/portfolio/stocks', 'Add stock to portfolio'],
        ['DELETE', '/api/portfolio/stocks/{ticker}', 'Remove stock'],
        ['GET', '/api/suggestions', 'Personalized BUY/SELL suggestions'],
        ['GET', '/api/stocks', 'List monitored stocks (filtered)'],
        ['GET', '/api/stocks/{ticker}/analysis', 'Latest analysis for ticker'],
        ['GET/PUT', '/api/preferences', 'User filter preferences'],
        ['GET', '/api/health', 'System health check'],
    ]
    t5 = Table(api_data, colWidths=[2*cm, 5.5*cm, 7*cm])
    t5.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, 1), (0, -1), 'Courier'),
        ('FONTNAME', (1, 1), (1, -1), 'Courier'),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#CBD5E1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LIGHT_BG]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t5)

    # Build the PDF
    doc.build(story)
    print(f"PDF generated: {OUTPUT_FILE}")


if __name__ == "__main__":
    build_pdf()
