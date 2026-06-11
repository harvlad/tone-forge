-- Test AppleScript Setup
-- Run this first to verify everything is configured correctly

property alsFolder : "/Users/mattharvey/Sites/tone-forge/backend/preset_catalog_output/als"
property audioFolder : "/Users/mattharvey/Sites/tone-forge/backend/preset_catalog_output/audio"

on run
	set issues to {}
	set info to {}

	-- Check ALS folder exists
	tell application "System Events"
		if exists folder alsFolder then
			set alsCount to count of (every file of folder alsFolder whose name extension is "als")
			set end of info to "✓ ALS folder exists: " & alsCount & " files"
		else
			set end of issues to "✗ ALS folder not found: " & alsFolder
		end if

		-- Check audio folder exists
		if exists folder audioFolder then
			set wavCount to count of (every file of folder audioFolder whose name extension is "wav")
			set end of info to "✓ Audio folder exists: " & wavCount & " files already rendered"
		else
			set end of issues to "✗ Audio folder not found: " & audioFolder
			set end of info to "  (Will be created on first export)"
		end if
	end tell

	-- Check Ableton is installed
	try
		tell application "System Events"
			set abletonInstalled to exists application file ((path to applications folder as text) & "Ableton Live 12 Standard.app")
		end tell
		if abletonInstalled then
			set end of info to "✓ Ableton Live 12 Standard found"
		else
			set end of issues to "✗ Ableton Live 12 Standard not found"
		end if
	on error
		set end of issues to "✗ Could not check for Ableton"
	end try

	-- Check if Ableton is running
	tell application "System Events"
		set abletonRunning to (name of processes) contains "Ableton Live 12 Standard"
	end tell
	if abletonRunning then
		set end of info to "✓ Ableton is running"
	else
		set end of info to "○ Ableton is not running (will be launched)"
	end if

	-- Build result message
	set msg to "ToneForge Rendering Setup Check" & return & return

	if (count of issues) > 0 then
		set msg to msg & "ISSUES:" & return
		repeat with issue in issues
			set msg to msg & "  " & issue & return
		end repeat
		set msg to msg & return
	end if

	set msg to msg & "STATUS:" & return
	repeat with item_info in info
		set msg to msg & "  " & item_info & return
	end repeat

	if (count of issues) = 0 then
		set msg to msg & return & "Ready to render! Run render_presets_fast.applescript"
		display dialog msg buttons {"OK"} default button "OK" with title "Setup OK ✓"
	else
		display dialog msg buttons {"OK"} default button "OK" with title "Setup Issues"
	end if
end run
