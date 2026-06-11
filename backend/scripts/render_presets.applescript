-- ToneForge Preset Rendering Assistant
--
-- This script automates the repetitive parts of preset rendering:
-- 1. Opens each ALS file in Ableton
-- 2. Waits for it to load
-- 3. Opens the Export Audio dialog (Cmd+Shift+R)
-- 4. YOU manually: verify settings, set filename, click Export
-- 5. Script waits for export to complete, then moves to next
--
-- Usage:
-- 1. Open this script in Script Editor
-- 2. Make sure Ableton Live is running
-- 3. Click Run
-- 4. Follow the prompts

-- Configuration
property alsFolder : "/Users/mattharvey/Sites/tone-forge/backend/preset_catalog_output/als"
property audioFolder : "/Users/mattharvey/Sites/tone-forge/backend/preset_catalog_output/audio"
property waitAfterOpen : 4 -- seconds to wait after opening ALS
property waitAfterExport : 3 -- seconds to wait after export completes

-- Get list of ALS files
on getALSFiles()
	tell application "System Events"
		set alsFiles to every file of folder alsFolder whose name extension is "als"
		set fileNames to {}
		repeat with f in alsFiles
			set end of fileNames to name of f
		end repeat
	end tell
	return fileNames
end getALSFiles

-- Check if audio file already exists
on audioExists(alsName)
	set wavName to text 1 thru -5 of alsName & ".wav"
	set wavPath to audioFolder & "/" & wavName
	tell application "System Events"
		return exists file wavPath
	end tell
end audioExists

-- Open ALS file in Ableton
on openALS(alsName)
	set alsPath to alsFolder & "/" & alsName
	tell application "Ableton Live 12 Standard"
		activate
		open POSIX file alsPath
	end tell
	delay waitAfterOpen
end openALS

-- Trigger Export Audio dialog
on triggerExport()
	tell application "System Events"
		tell process "Ableton Live 12 Standard"
			-- Cmd+Shift+R for Export Audio/Video
			keystroke "r" using {command down, shift down}
		end tell
	end tell
	delay 1
end triggerExport

-- Wait for user to complete export
on waitForExportComplete(expectedWavName)
	set wavPath to audioFolder & "/" & expectedWavName
	set maxWait to 120 -- max 2 minutes per export
	set waited to 0

	repeat while waited < maxWait
		delay 2
		set waited to waited + 2

		tell application "System Events"
			if exists file wavPath then
				delay waitAfterExport -- let file finish writing
				return true
			end if
		end tell
	end repeat

	return false
end waitForExportComplete

-- Main script
on run
	-- Get ALS files
	set alsFiles to getALSFiles()
	set totalFiles to count of alsFiles

	if totalFiles = 0 then
		display dialog "No ALS files found in:" & return & alsFolder buttons {"OK"} default button "OK"
		return
	end if

	-- Count already rendered
	set alreadyRendered to 0
	repeat with alsName in alsFiles
		if audioExists(alsName) then
			set alreadyRendered to alreadyRendered + 1
		end if
	end repeat

	set toRender to totalFiles - alreadyRendered

	-- Confirm start
	set startMsg to "Found " & totalFiles & " ALS files." & return & return
	set startMsg to startMsg & "Already rendered: " & alreadyRendered & return
	set startMsg to startMsg & "To render: " & toRender & return & return
	set startMsg to startMsg & "Export settings to use:" & return
	set startMsg to startMsg & "  • File Type: WAV" & return
	set startMsg to startMsg & "  • Sample Rate: 44100" & return
	set startMsg to startMsg & "  • Bit Depth: 16" & return
	set startMsg to startMsg & "  • Render Length: 10 seconds" & return
	set startMsg to startMsg & "  • Save to: " & audioFolder & return & return
	set startMsg to startMsg & "Ready to begin?"

	set userChoice to button returned of (display dialog startMsg buttons {"Cancel", "Start Rendering"} default button "Start Rendering")

	if userChoice is "Cancel" then
		return
	end if

	-- Make sure Ableton is running
	tell application "Ableton Live 12 Standard"
		activate
	end tell
	delay 2

	-- Process each file
	set renderedCount to 0
	set skippedCount to 0
	set currentIndex to 0

	repeat with alsName in alsFiles
		set currentIndex to currentIndex + 1
		set wavName to text 1 thru -5 of alsName & ".wav"

		-- Skip if already rendered
		if audioExists(alsName) then
			set skippedCount to skippedCount + 1
		else
			-- Show progress
			set progressMsg to "Rendering " & currentIndex & "/" & totalFiles & return & return
			set progressMsg to progressMsg & "Current: " & alsName & return & return
			set progressMsg to progressMsg & "Opening in Ableton..."

			display notification progressMsg with title "ToneForge Preset Rendering"

			-- Open ALS
			openALS(alsName)

			-- Trigger export dialog
			triggerExport()

			-- Prompt user
			set exportMsg to "EXPORT: " & alsName & return & return
			set exportMsg to exportMsg & "In the Export dialog:" & return
			set exportMsg to exportMsg & "1. Set filename to: " & wavName & return
			set exportMsg to exportMsg & "2. Save to: " & audioFolder & return
			set exportMsg to exportMsg & "3. Click Export" & return & return
			set exportMsg to exportMsg & "Click 'Done' after export completes."

			set userAction to button returned of (display dialog exportMsg buttons {"Skip", "Done", "Stop"} default button "Done" with title "Export Preset " & currentIndex & "/" & totalFiles)

			if userAction is "Stop" then
				exit repeat
			else if userAction is "Done" then
				set renderedCount to renderedCount + 1
			else
				set skippedCount to skippedCount + 1
			end if
		end if
	end repeat

	-- Summary
	set summaryMsg to "Rendering Complete!" & return & return
	set summaryMsg to summaryMsg & "Rendered: " & renderedCount & return
	set summaryMsg to summaryMsg & "Skipped: " & skippedCount & return
	set summaryMsg to summaryMsg & "Total: " & totalFiles & return & return
	set summaryMsg to summaryMsg & "Audio saved to:" & return & audioFolder

	display dialog summaryMsg buttons {"OK"} default button "OK" with title "ToneForge Complete"
end run
