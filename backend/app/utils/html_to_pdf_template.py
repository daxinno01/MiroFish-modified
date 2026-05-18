HTML_TO_PDF_TEMPLATE = """\
<html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {{ 
                size: A4;
                margin: 1.5cm 1.5cm; /* Tighter margins to match professional reports */
                @bottom-right {{
                    content: "Page " counter(page) " of " counter(pages);
                    font-size: 8pt;
                    color: #000;
                }}
            }}

            body {{ 
                font-family: "Helvetica", "Arial", "Noto Sans SC", sans-serif; 
                font-size: 10pt; /* Smaller, standardized professional size */
                color: #000; /* Strict black text as requested */
                line-height: 1.4;
                text-align: justify;
                margin: 0;
                padding: 0;
            }}

            /* Reduce spacing between paragraphs */
            p {{ 
                margin-top: 0; 
                margin-bottom: 8pt; 
            }}

            /* Report Header */
            h1 {{ 
                font-size: 18pt; 
                font-weight: bold;
                border-bottom: 2px solid #000;
                padding-bottom: 5pt;
                margin-bottom: 12pt;
                text-transform: uppercase;
                text-align: left;
            }}

            /* Section Headers */
            h2 {{ 
                font-size: 12pt; 
                font-weight: bold;
                margin-top: 15pt;
                margin-bottom: 5pt;
                border-bottom: 0.5pt solid #eee;
            }}

            h3 {{ 
                font-size: 10.5pt; 
                font-weight: bold;
                margin-top: 10pt;
                margin-bottom: 4pt;
                font-style: italic;
            }}

            /* Lists - keeping them compact */
            ul {{ 
                margin-top: 0;
                margin-bottom: 8pt;
                padding-left: 15pt;
            }}
            li {{ 
                margin-bottom: 3pt;
            }}

            /* Source tag styling (the tags) */
            span.source-tag {{
                font-size: 8pt;
                color: #666;
                vertical-align: super;
            }}

            /* Force black for all elements */
            * {{ color: #000 !important; }}
            
            /* Exception: lighter lines for tables */
            table {{ 
                width: 100%; 
                border-collapse: collapse; 
                margin: 10pt 0; 
            }}
            td, th {{ 
                border: 0.5pt solid #ccc; 
                padding: 4pt; 
                font-size: 9pt; 
            }}
        </style>
    </head>
    <body>
        {translated_content}
    </body>
</html>\
"""