-- ToneForge Preset Rendering (Fast Mode)
--
-- Faster version that:
-- 1. Opens ALS file
-- 2. Opens Export dialog
-- 3. Auto-fills the filename via clipboard paste
-- 4. You just click Export and wait
--
-- Usage:
-- 1. Open in Script Editor
-- 2. Make sure Ableton is running
-- 3. Click Run

-- Configuration
property alsFolder : "/Users/mattharvey/Sites/tone-forge/backend/preset_catalog_output/als"
property audioFolder : "/Users/mattharvey/Sites/tone-forge/backend/preset_catalog_output/audio"
property waitAfterOpen : 3
property renderLengthBars : 12 -- ~10 seconds at 120 BPM

on getALSFiles()
	set alsFiles to {}
	tell application "System Events"
		set allFiles to every file of folder alsFolder whose name extension is "als"
		repeat with f in allFiles
			set end of alsFiles to name of f
		end repeat
	end tell
	return alsFiles
end getALSFiles

on audioExists(alsName)
	set wavName to text 1 thru -5 of alsName & ".wav"
	set wavPath to audioFolder & "/" & wavName
	tell application "System Events"
		return exists file wavPath
	end tell
end audioExists

on openALS(alsName)
	set alsPath to alsFolder & "/" & alsName
	-- Use shell open command which is more reliable
	do shell script "open " & quoted form of alsPath
	delay 1
	tell application "Ableton Live 12 Standard"
		activate
	end tell
	delay waitAfterOpen
end openALS

on triggerExportWithPath(wavName)
	-- macOS "Go to folder" only accepts FOLDER paths; pasting a full
	-- file path causes it to silently reject and the dialog drifts to
	-- the last-used location. Pass the folder only.
	set the clipboard to audioFolder

	tell application "System Events"
		tell process "Ableton Live 12 Standard"
			-- Cmd+Shift+R for Export Audio/Video
			keystroke "r" using {command down, shift down}
		end tell
	end tell

	delay 1.5 -- Wait for dialog

	tell application "System Events"
		tell process "Ableton Live 12 Standard"
			-- Cmd+Shift+G to open "Go to folder" in save dialog
			keystroke "g" using {command down, shift down}
			delay 0.5

			-- Paste the folder path
			keystroke "v" using {command down}
			delay 0.3

			-- Press Enter to navigate to that folder
			keystroke return
			delay 0.5
		end tell
	end tell
end triggerExportWithPath

on run
	set alsFiles to getALSFiles()
	set totalFiles to count of alsFiles
	
	if totalFiles = 0 then
		display dialog "No ALS files found in:" & return & alsFolder buttons {"OK"}
		return
	end if
	
	-- Count pending
	set pendingFiles to {}
	repeat with alsName in alsFiles
		if not audioExists(alsName) then
			set end of pendingFiles to alsName
		end if
	end repeat
	
	set pendingCount to count of pendingFiles
	
	if pendingCount = 0 then
		display dialog "All " & totalFiles & " presets already rendered!" buttons {"OK"}
		return
	end if
	
	-- Confirm
	set msg to "Ready to render " & pendingCount & " presets." & return & return
	set msg to msg & "For each preset, the script will:" & return
	set msg to msg & "1. Open the ALS file" & return
	set msg to msg & "2. Open Export dialog" & return
	set msg to msg & "3. Navigate to output folder" & return & return
	set msg to msg & "You just need to click 'Export' each time." & return & return
	set msg to msg & "Start?"
	
	if button returned of (display dialog msg buttons {"Cancel", "Start"} default button "Start") is "Cancel" then
		return
	end if
	
	-- Ensure Ableton is frontmost
	tell application "Ableton Live 12 Standard" to activate
	delay 1
	
	set renderedCount to 0
	set idx to 0
	
	repeat with alsName in pendingFiles
		set idx to idx + 1
		set wavName to text 1 thru -5 of alsName & ".wav"

		-- Progress notification
		display notification "Opening: " & alsName with title "Preset " & (idx as text) & "/" & (pendingCount as text)

		-- Open ALS
		openALS(alsName)

		-- Open export dialog with path
		triggerExportWithPath(wavName)

		-- Wait for user to export
		set action to button returned of (display dialog "Preset: " & alsName & return & return & "Click 'Export' in Ableton, then click 'Next' here." & return & return & "Output: " & wavName buttons {"Stop", "Skip", "Next"} default button "Next" with title ((idx as text) & "/" & (pendingCount as text)))

		if action is "Stop" then
			exit repeat
		else if action is "Next" then
			set renderedCount to renderedCount + 1
			delay 1 -- Brief pause between presets
		end if
	end repeat

	-- Done
	display dialog "Done!" & return & return & "Rendered: " & (renderedCount as text) & "/" & (pendingCount as text) buttons {"OK"} with title "Complete"
end run
