"""HTML constants for visualizations."""

HTML_DEFAULT_HEAD_FOR_COMP_v2: str = r"""<head>
<link rel="icon" type="image/png"
href="https://ds4sd.github.io/docling/assets/logo.png"/>
<meta charset="UTF-8">
<title>
Powered by Docling
</title>
<style>
html {
  background-color: LightGray;
}
body {
  margin: 0 auto;
  padding: 10px;
  background-color: White;
  font-family: Arial, sans-serif;
  box-shadow: 10px 10px 10px grey;
  font-size: 0.9em; /* Smaller text */
  max-width: 100%;
}
td {
  width: 25%;
}
.page td {
  width:auto;
}
/* Create a flex container for columns */
.container {
  display: flex;
  flex-wrap: nowrap;
  width: 100%;
  gap: 10px;
}
/* Each column takes exactly 25% width */
.column {
  flex: 0 0 25%;
  padding: 10px;
  box-sizing: border-box;
  overflow-x: auto; /* Enable horizontal scrolling within each column */
}
figure {
  display: block;
  width: 100%;
  margin: 0;
  margin-top: 10px;
  margin-bottom: 10px;
  overflow-x: auto; /* Horizontal scrolling for figures */
}
img {
  display: block;
  margin: auto;
  margin-top: 10px;
  margin-bottom: 10px;
  max-width: 100%; /* Images will be responsive within their container */
  min-width: 300px;
  height: auto;
}
.page img {
  min-width: auto;
}
.table-container {
  width: 100%;
  overflow-x: auto; /* Horizontal scrolling for tables */
}
table {
  min-width: 500px;
  background-color: White;
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
}
th, td {
  border: 1px solid black;
  padding: 8px;
  text-align: left;
}
th {
  font-weight: bold;
  background-color: #f2f2f2;
}
table tr:nth-child(even) td {
  background-color: LightGray;
}
/* Media query for responsive behavior */
@media (max-width: 768px) {
  .container {
    flex-direction: column;
  }
  
  .column {
    flex: 0 0 100%;
    margin-bottom: 15px;
  }
}
</style>
</head>"""
