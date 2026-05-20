# Single-cell spike rasters to visualize event-locked responses
When exploring how cells change firing rates around task events, plot spike rasters for several selected example cells.  Think how to sort the trial rows, e.g. by
- signed stimulus contrast (ipsi vs contra)
- behavioral response latency of each trial
- prestimulus firing rate
- evoked firing rate or evoked minus prestimulus
- chronologically

# Selecting example cells
Consider Benjamini-Hochberg based on p-values for each cell.

# Population rasters
When exploring population coding, consider population rasters with one row per cell. Think how to sort the cell rows, e.g. by
- depth on probe/brain region
- mean response latency to event of interest
- firing rate evoked by event of interest
- baseline rate

# Jittering 
In scatter plots, discrete variables should be jittered.