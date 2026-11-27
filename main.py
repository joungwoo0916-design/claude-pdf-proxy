version: 1.2.1
cache: true

fileConfig:
  serverFileSizeLimit: 50
  legacyFileUploadUX: false
  endpoints:
    default:
      fileLimit: 10
      fileSizeLimit: 50
      totalSizeLimit: 100
      supportedMimeTypes:
        - "application/pdf"
        - "text/.*"
        - "application/vnd.openxmlformats-officedocument.*"
        - "application/vnd.ms-excel"
        - "image/.*"

endpoints:
  custom:
    - name: "Claude-Enterprise"
      apiKey: "dummy-key"
      baseURL: "https://[내_프록시_주소]/v1"  # 본인 프록시 주소
      models:
        default: ["claude-sonnet-5.5"]
      titleConvo: true
      titleModel: "claude-sonnet-5.5"

modelSpecs:
  enforce: true
  prioritize: true
  list:
    - name: "claude-sonnet-5.5"
      label: "Claude Sonnet 5.5 (사내 표준 / 도면 자동분석)"
      default: true
      artifacts: true
      preset:
        endpoint: "Claude-Enterprise"
        model: "claude-sonnet-5.5"
        promptPrefix: |
          [시스템 절대 원칙: 전사 오피스, 도면 분석 및 시각화 자동화 지침]
          당신은 엔지니어링 도면 분석, 기업용 프레젠테이션, 엑셀 데이터 제작 최고 전문가입니다.
          1. 첨부된 도면 이미지에서 표제란(도번, 품명, 재질, 승인일자), 부품 목록(BOM), 주요 치수, 주기사항을 표(Table)로 도출하십시오.
          2. PPT 제작 요청 시 :::artifact{identifier="office-deck" type="text/html" title="결과물 미리보기"} 태그 및 PptxGenJS CDN 연동 다운로드 버튼을 만드십시오.
          3. 엑셀 정리 요청 시 SheetJS CDN 연동 및 [⬇ 엑셀(.xlsx) 다운로드] 버튼을 만드십시오.

    - name: "gemini-3.5-flash-lite"
      label: "Gemini 3.5 Flash (대용량/초고속 전용)"
      artifacts: true
      preset:
        endpoint: "google"
        model: "gemini-3.5-flash-lite"
