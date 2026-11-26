-- Lecture de PDF avec les frameworks intégrés à macOS (aucune installation) :
--   PDFKit pour le texte des PDF numériques, Vision pour l'OCR des pages scannées.
-- Usage : osascript macos_pdf.applescript /chemin/document.pdf
-- Sortie : pour chaque page, une ligne "<<<PAGE n texte|ocr>>>" puis le texte.
--          Pour l'OCR, chaque ligne est préfixée de "y<TAB>x<TAB>" (coordonnées Vision) pour le tri.
use AppleScript version "2.7"
use framework "Foundation"
use framework "AppKit"
use framework "PDFKit"
use framework "Vision"
use scripting additions

on run argv
	set pdfPath to item 1 of argv
	set theURL to current application's |NSURL|'s fileURLWithPath:pdfPath
	set pdfDoc to current application's PDFDocument's alloc()'s initWithURL:theURL
	if pdfDoc is missing value then return "<<<ERREUR PDF illisible>>>"
	set pageCount to (pdfDoc's pageCount()) as integer
	set out to current application's NSMutableArray's array()
	repeat with i from 0 to pageCount - 1
		set thePage to (pdfDoc's pageAtIndex:i)
		set pageText to ""
		try
			set s to thePage's |string|()
			if s is not missing value then set pageText to s as text
		end try
		if (length of pageText) > 80 then
			(out's addObject:("<<<PAGE " & (i + 1) & " texte>>>"))
			(out's addObject:pageText)
		else
			(out's addObject:("<<<PAGE " & (i + 1) & " ocr>>>"))
			try
				set theImage to (thePage's thumbnailOfSize:{1700, 2400} forBox:0)
				set cgImage to (theImage's CGImageForProposedRect:(missing value) context:(missing value) hints:(missing value))
				set reqHandler to (current application's VNImageRequestHandler's alloc()'s initWithCGImage:cgImage options:(current application's NSDictionary's dictionary()))
				set req to current application's VNRecognizeTextRequest's alloc()'s init()
				(req's setRecognitionLevel:0)
				(req's setUsesLanguageCorrection:true)
				try
					(req's setRecognitionLanguages:{"fr-FR", "en-US"})
				end try
				(reqHandler's performRequests:{req} |error|:(missing value))
				set obs to req's results()
				repeat with o in obs
					set cand to ((o's topCandidates:1)'s firstObject())
					if cand is not missing value then
						set lineText to (cand's |string|()) as text
						set yx to "0" & tab & "0"
						try
							set bb to o's boundingBox()
							set {{bx, by}, {bw, bh}} to bb
							set yx to ((round (by * 10000)) as text) & tab & ((round (bx * 10000)) as text)
						end try
						(out's addObject:(yx & tab & lineText))
					end if
				end repeat
			on error errMsg
				(out's addObject:("<<<ERREUR OCR " & errMsg & ">>>"))
			end try
		end if
	end repeat
	return (out's componentsJoinedByString:linefeed) as text
end run
